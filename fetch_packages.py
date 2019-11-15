#!/usr/bin/python
#
# Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
#
import os
import errno
import re
import shutil
import subprocess
import sys, getopt
import platform
from time import sleep

_RETRIES = 5
_OPT_VERBOSE = None
_OPT_DRY_RUN = None
_PACKAGE_CACHE='/tmp/cache/' + os.environ['USER'] + '/webui_third_party'
_NODE_MODULES='./node_modules'
_TMP_NODE_MODULES=_PACKAGE_CACHE + '/' + _NODE_MODULES
_TAR_COMMAND = ['tar', '--no-same-owner']
_CACHED_PKG_DISTROS = ('Ubuntu', 'Red Hat', 'CentOS', 'darwin')

from lxml import objectify

def getFilename(pkg, url):
    element = pkg.find("local-filename")
    if element:
        return str(element)

    (path, filename) = url.rsplit('/', 1)
    m = re.match(r'\w+\?\w+=(.*)', filename)
    if m:
        filename = m.group(1)
    return filename

def setTarCommand():
    if isTarGnuVersion():
        print 'GNU tar found. we will skip the no-unknown-keyword warning'
        global _TAR_COMMAND
        _TAR_COMMAND = ['tar', '--no-same-owner', '--warning=no-unknown-keyword']
    else:
        print 'No GNU tar. will use default tar utility'

def isTarGnuVersion():
    cmd = subprocess.Popen(['tar', '--version'],
                           stdout=subprocess.PIPE)
    (output, _) = cmd.communicate()
    (first, _) = output.split('\n', 1)
    if first.lower().find('gnu') != -1:
        return True
    return False

def getTarDestination(tgzfile, compress_flag):
    cmd = subprocess.Popen( _TAR_COMMAND + [ '--exclude=.*','-' + compress_flag + 'tf', tgzfile],
                           stdout=subprocess.PIPE)
    (output, _) = cmd.communicate()
    (first, _) = output.split('\n', 1)
    fields = first.split('/')
    return fields[0]

def getZipDestination(tgzfile):
    cmd = subprocess.Popen(['unzip', '-t', tgzfile],
                           stdout=subprocess.PIPE)
    (output, _) = cmd.communicate()
    lines = output.split('\n')
    for line in lines:
        print line
        m = re.search(r'testing:\s+([\w\-\.]+)\/', line)
        if m:
            return m.group(1)
    return None

def getFileDestination(file):
    start = file.rfind('/')
    if start < 0:
        return None
    return file[start+1:]

def ApplyPatches(pkg):
    stree = pkg.find('patches')
    if stree is None:
        return
    for patch in stree.getchildren():
        cmd = ['patch']
        if patch.get('strip'):
            cmd.append('-p')
            cmd.append(patch.get('strip'))
        if _OPT_VERBOSE:
            print "Patching %s <%s..." % (' '.join(cmd), str(patch))
        if not _OPT_DRY_RUN:
            fp = open(str(patch), 'r')
            proc = subprocess.Popen(cmd, stdin = fp)
            proc.communicate()

#def VarSubst(cmdstr, filename):
#    return re.sub(r'\${filename}', filename, cmdstr)

def GetOSDistro():
    distro = ''
    if sys.platform == 'darwin':
        return sys.platform
    else:
        try:
            return platform.linux_distribution()[0]
        except:
            pass
    return distro

def DownloadPackage(url, ccfile, pkg):
    md5 = pkg.md5
    pkg.ccfile = ccfile
    if url.find('$distro') != -1:
        # Platform specific package download

        distro = GetOSDistro()
        if distro == '':
            md5 = md5.other
            # Remove the $distro from the url and try
            url = url.replace('/$distro', '')
            # Change the pkg format to npm download the dependencies
            if pkg.format == 'npm-cached':
                pkg.format = 'npm'
        else:
            # check if we have the distro in our cache
            found = False
            for cached_pkg in _CACHED_PKG_DISTROS:
                if cached_pkg in distro:
                    distro = cached_pkg
                    found = True
                    break
            if found == False:
                # Remove the $distro from the url and try
                url = url.replace('/$distro', '')
                # Change the pkg format to npm download the dependencies
                md5 = md5.other
                if pkg.format == 'npm-cached':
                    pkg.format = 'npm'
            else:
                distro = distro.lower().replace(" ", "")
                url = url.replace('$distro', distro)
                md5 = md5[distro]
                pkg.distro = distro
                # Change the ccfile, add distro before the package name
                idx = ccfile.rfind("/")
                pkgCachePath = ccfile[:idx] + "/" + distro
                pkg.pkgCachePath = pkgCachePath
                pkg.ccfile = pkgCachePath + "/" + ccfile[idx + 1:]
                ccfile = pkg.ccfile.text
                # Now create the directory
                try:
                    os.makedirs(pkgCachePath)
                except OSError:
                    pass
        print url

    #Check if the package already exists
    if os.path.isfile(ccfile):
        md5sum = FindMd5sum(ccfile)
        if md5sum == md5:
            return pkg
        else:
            os.remove(ccfile)
    
    retry_count = 0
    while True:
        subprocess.call(['wget', '--no-check-certificate', '-O', ccfile, url])
        md5sum = FindMd5sum(ccfile)
        if _OPT_VERBOSE:
            print "Calculated md5sum: %s" % md5sum
            print "Expected md5sum: %s" % md5
        if md5sum == md5:
            return pkg
        elif retry_count <= _RETRIES:
            os.remove(ccfile)
            retry_count += 1
            sleep(1)
            continue
        else:
            raise RuntimeError("MD5sum %s, expected(%s) dosen't match for the "
                               "downloaded package %s" % (md5sum, md5, ccfile))
    return pkg

def ProcessPackage(pkg):
    print "Processing %s ..." % (pkg['name'])
    url = str(pkg['url'])
    filename = getFilename(pkg, url)
    ccfile = _PACKAGE_CACHE + '/' + filename
    installArguments = pkg.find('install-arguments')
    if pkg.format == 'npm-cached':
        try:
            shutil.rmtree(str(_NODE_MODULES + '/' + pkg['name']))
        except OSError as exc:
            pass
        try:
            os.makedirs(_NODE_MODULES)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                pass
            else:
                print 'mkdirs of ' + _NODE_MODULES + ' failed.. Exiting..'
                return
        #ccfile = _NODE_MODULES + '/' + filename
    pkg = DownloadPackage(url, ccfile, pkg)

    #
    # Determine the name of the directory created by the package.
    # unpack-directory means that we 'cd' to the given directory before
    # unpacking.
    #
    ccfile = pkg.ccfile.text
    dest = None
    unpackdir = pkg.find('unpack-directory')
    if unpackdir:
        dest = str(unpackdir)
    else:
        if pkg.format == 'tgz':
            dest = getTarDestination(ccfile, 'z')
        elif pkg.format == 'npm-cached':
            dest = _NODE_MODULES + '/' + getTarDestination(ccfile, 'z')
        elif pkg.format == 'tbz':
            dest = getTarDestination(ccfile, 'j')
        elif pkg.format == 'zip':
            dest = getZipDestination(ccfile)
        elif pkg.format == 'npm':
            dest = getTarDestination(ccfile, 'z')
        elif pkg.format == 'file':
            dest = getFileDestination(ccfile)

    #
    # clean directory before unpacking and applying patches
    #
    rename = pkg.find('rename')
    if rename and pkg.format == 'npm-cached':
        rename = _NODE_MODULES + '/' + str(rename)
    if rename and os.path.isdir(str(rename)):
        if not _OPT_DRY_RUN:
          shutil.rmtree(str(rename))

    elif dest and os.path.isdir(dest):
        if _OPT_VERBOSE:
            print "Clean directory %s" % dest
        if not _OPT_DRY_RUN:
            shutil.rmtree(dest)

    if unpackdir:
        try:
            os.makedirs(str(unpackdir))
        except OSError as exc:
            pass

    cmd = None
    if pkg.format == 'tgz':
        cmd = _TAR_COMMAND + ['-zxf', ccfile]
    elif pkg.format == 'tbz':
        cmd = _TAR_COMMAND + ['-jxf', ccfile]
    elif pkg.format == 'zip':
        cmd = ['unzip', '-o', ccfile]
    elif pkg.format == 'npm':
        newDir = _PACKAGE_CACHE
        if 'distro' in pkg:
            newDir = newDir + pkg.distro
        cmd = ['npm', 'install', ccfile, '--prefix', newDir]
        if installArguments:
            cmd.append(str(installArguments))
    elif pkg.format == 'file':
        cmd = ['cp', '-af', ccfile, dest]
    elif pkg.format == 'npm-cached':
        cmd = _TAR_COMMAND + ['-zxf', ccfile, '-C', _NODE_MODULES]
    else:
        print 'Unexpected format: %s' % (pkg.format)
        return

    print 'Issuing command: %s' % (cmd)

    if not _OPT_DRY_RUN:
        cd = None
        if unpackdir:
            cd = str(unpackdir)
        if pkg.format == 'npm':
            try:
                os.makedirs(_NODE_MODULES)
                os.makedirs(newDir)
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    pass
                else:
                    print 'mkdirs of ' + _NODE_MODULES + ' ' + newDir + ' failed.. Exiting..'
                    return

            npmCmd = ['cp', '-af', newDir + "/" + _NODE_MODULES + '/' + pkg['name'],
                      './node_modules/']
            if os.path.exists(newDir + '/' + pkg['name']):
                cmd = npmCmd
            else:
		try:
                   p = subprocess.Popen(cmd, cwd = cd)
                   ret = p.wait()
                   if ret is not 0:
                       sys.exit('Terminating: ProcessPackage with return code: %d' % ret);
                   cmd = npmCmd
		except OSError:
		   print ' '.join(cmd) + ' could not be executed, bailing out!'
		   return

        p = subprocess.Popen(cmd, cwd = cd)
        ret = p.wait()
        if ret is not 0:
            sys.exit('Terminating: ProcessPackage with return code: %d' % ret);

    if rename and dest:
        os.rename(dest, str(rename))

    ApplyPatches(pkg)

def FindMd5sum(anyfile):
    if sys.platform == 'darwin':
        cmd = ['md5', '-r']
    else:
        cmd = ['md5sum']
    cmd.append(anyfile)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.PIPE)
    stdout, stderr = proc.communicate()
    md5sum = stdout.split()[0]
    return md5sum

def main(filename):
    tree = objectify.parse(filename)
    root = tree.getroot()
    #Check which version of tar is used and skip warning messages.
    setTarCommand()
    for object in root.iterchildren():
        if object.tag == 'package':
            ProcessPackage(object)

if __name__ == '__main__':
    try:
        opts,args = getopt.getopt(sys.argv[1:],"f:",["file="])
    except getopt.GetoptError:
        raise RuntimeError("Error in parsing the options/arguments")
    xmlfile = None
    for opt,arg in opts:
        if opt in ("-f","--file"):
            xmlfile = arg
    os.chdir(os.path.dirname(os.path.realpath(__file__)))
    try:
        os.makedirs(_PACKAGE_CACHE)
    except OSError:
        pass

    if xmlfile == None:
        main('packages.xml')
    else:
        main(xmlfile)

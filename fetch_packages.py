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
from time import sleep

_RETRIES = 5
_OPT_VERBOSE = None
_OPT_DRY_RUN = None
_PACKAGE_CACHE='/tmp/cache/' + os.environ['USER'] + '/webui_third_party'
_NODE_MODULES='./node_modules'

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

def getTarDestination(tgzfile, compress_flag):
    cmd = subprocess.Popen(['tar', compress_flag + 'tf', tgzfile],
                           stdout=subprocess.PIPE)
    (output, _) = cmd.communicate()
    (first, _) = output.split('\n', 1)
    fields = first.split()
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

def DownloadPackage(url, pkg, md5):
    #Check if the package already exists
    pkgExists = True
    if os.path.isfile(pkg):
        md5sum = FindMd5sum(pkg)
        if md5sum == md5:
            return pkgExists
        else:
            os.remove(pkg)
    
    pkgExists = False
    retry_count = 0
    while True:
        subprocess.call(['wget', '--no-check-certificate', '-O', pkg, url])
        md5sum = FindMd5sum(pkg)
        if _OPT_VERBOSE:
            print "Calculated md5sum: %s" % md5sum
            print "Expected md5sum: %s" % md5
        if md5sum == md5:
            return pkgExists
        elif retry_count <= _RETRIES:
            os.remove(pkg)
            retry_count += 1
            sleep(1)
            continue
        else:
            raise RuntimeError("MD5sum %s, expected(%s) dosen't match for the "
                               "downloaded package %s" % (md5sum, md5, pkg))

def ProcessPackage(pkg):
    print "Processing %s ..." % (pkg['name'])
    url = str(pkg['url'])
    filename = getFilename(pkg, url)
    ccfile = _PACKAGE_CACHE + '/' + filename
    pkgExists = DownloadPackage(url, ccfile, pkg.md5)

    #
    # Determine the name of the directory created by the package.
    # unpack-directory means that we 'cd' to the given directory before
    # unpacking.
    #
    dest = None
    unpackdir = pkg.find('unpack-directory')
    if unpackdir:
        dest = str(unpackdir)
    else:
        if pkg.format == 'tgz':
            dest = getTarDestination(ccfile, 'z')
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
        

    if pkg.format == 'tgz':
        cmd = ['tar', 'zxvf', ccfile]
    elif pkg.format == 'tbz':
        cmd = ['tar', 'jxvf', ccfile]
    elif pkg.format == 'zip':
        cmd = ['unzip', '-o', ccfile]
    elif pkg.format == 'npm':
        cmd = ['npm', 'install', ccfile, '--prefix', '.']
    elif pkg.format == 'file':
        cmd = ['cp', '-af', ccfile, dest]
    else:
        print 'Unexpected format: %s' % (pkg.format)
        return

    if not _OPT_DRY_RUN:
        cd = None
        if unpackdir:
            cd = str(unpackdir)
        if pkg.format == 'npm':
            try:
                os.makedirs(_NODE_MODULES)
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    pass
                else:
                    print 'mkdirs of ' + _NODE_MODULES + ' failed.. Exiting..'
                    return

            if os.path.exists(_NODE_MODULES + '/' + pkg['name']):
                if True == pkgExists:
                    return
                else:
                    shutil.rmtree(_NODE_MODULES + '/' + pkg['name']);
            else:
                try:
                   p = subprocess.Popen(cmd, cwd = cd)
                   p.wait()
                except OSError:
                    print ' '.join(cmd) + ' could not be executed, bailing out!'
                    return
                return
        p = subprocess.Popen(cmd, cwd = cd)
        p.wait()

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

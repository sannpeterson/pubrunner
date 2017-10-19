
import pubrunner
import sys
import argparse
import os
import git
import tempfile
import shutil
import logging
import traceback
import yaml
import json
import subprocess
import shlex
import wget
import gzip
import hashlib
import six
import six.moves.urllib as urllib
import time
from six.moves import reload_module
import ftplib
import ftputil
from collections import OrderedDict
import re
import math
import tarfile

def checkFileSuffixFilter(filename,fileSuffixFilter):
	if fileSuffixFilter is None:
		return True
	elif filename.endswith('.tar.gz') or filename.endswith('.gz'):
		return True
	elif filename.endswith(fileSuffixFilter):
		return True
	else:
	 	return False

def download(url,out,fileSuffixFilter=None):
	if url.startswith('ftp'):
		url = url.replace("ftp://","")
		hostname = url.split('/')[0]
		path = "/".join(url.split('/')[1:])
		with ftputil.FTPHost(hostname, 'anonymous', 'secret') as host:
			downloadFTP(path,out,host,fileSuffixFilter)
	elif url.startswith('http'):
		downloadHTTP(url,out,fileSuffixFilter)
	else:
		raise RuntimeError("Unsure how to download file. Expecting URL to start with ftp or http. Got: %s" % url)

def downloadFTP(path,out,host,fileSuffixFilter=None):
	if host.path.isfile(path):
		remoteTimestamp = host.path.getmtime(path)
		
		doDownload = True
		if not checkFileSuffixFilter(path,fileSuffixFilter):
			doDownload = False

		if os.path.isdir(out):
			localTimestamp = os.path.getmtime(out)
			if not remoteTimestamp > localTimestamp:
				doDownload = False
		if path.endswith('.gz'):
			outUnzipped = out[:-3]
			if os.path.isfile(outUnzipped):
				localTimestamp = os.path.getmtime(outUnzipped)
				if not remoteTimestamp > localTimestamp:
					doDownload = False
		if doDownload:
			print("  Downloading %s" % path)
			didDownload = host.download(path,out)
			os.utime(out,(remoteTimestamp,remoteTimestamp))
		else:
			print("  Skipping %s" % path)

	elif host.path.isdir(path):
		basename = host.path.basename(path)
		newOut = os.path.join(out,basename)
		if not os.path.isdir(newOut):
			os.makedirs(newOut)
		for child in host.listdir(path):
			srcFilename = host.path.join(path,child)
			dstFilename = os.path.join(newOut,child)
			downloadFTP(srcFilename,dstFilename,host,fileSuffixFilter)
	else:
		raise RuntimeError("Path (%s) is not a file or directory" % path) 

def downloadHTTP(url,out,fileSuffixFilter=None):
	if not checkFileSuffixFilter(url,fileSuffixFilter):
		return

	fileAlreadyExists = os.path.isfile(out)

	if fileAlreadyExists:
		timestamp = os.path.getmtime(out)
		beforeHash = pubrunner.calcSHA256(out)
		os.unlink(out)

	wget.download(url,out,bar=None)
	if fileAlreadyExists:
		afterHash = pubrunner.calcSHA256(out)
		if beforeHash == afterHash: # File hasn't changed so move the modified date back
			os.utime(out,(timestamp,timestamp))

def gunzip(source,dest,deleteSource=False):
	timestamp = os.path.getmtime(source)
	with gzip.open(source, 'rb') as f_in, open(dest, 'wb') as f_out:
		shutil.copyfileobj(f_in, f_out)
	os.utime(dest,(timestamp,timestamp))

	if deleteSource:
		os.unlink(source)
	
# https://stackoverflow.com/questions/312443/how-do-you-split-a-list-into-evenly-sized-chunks
def chunks(l, n):
	"""Yield successive n-sized chunks from l."""
	for i in range(0, len(l), n):
		yield l[i:i + n]


def generatePubmedHashes(inDir,outDir):
	snakeFile = os.path.join(pubrunner.__path__[0],'Snakefiles','PubmedHashes.py')
	parameters = {'INDIR':inDir,'OUTDIR':outDir}
	pubrunner.launchSnakemake(snakeFile,parameters=parameters)
	


def getResource(resource):
	print("Fetching resource: %s" % resource)

	globalSettings = pubrunner.getGlobalSettings()
	resourceDir = os.path.expanduser(globalSettings["storage"]["resources"])
	thisResourceDir = os.path.join(resourceDir,resource)

	packagePath = os.path.dirname(pubrunner.__file__)
	resourceYamlPath = os.path.join(packagePath,'resources','%s.yml' % resource)
	assert os.path.isfile(resourceYamlPath), "Can not find appropriate file for resource: %s" % resource

	with open(resourceYamlPath) as f:
		resourceInfo = yaml.load(f)

	#print(json.dumps(resourceInfo,indent=2))

	if resourceInfo['type'] == 'git':
		assert isinstance(resourceInfo['url'], six.string_types), 'The URL for a git resource must be a single address'

		if os.path.isdir(thisResourceDir):
			# Assume it is an existing git repo
			repo = git.Repo(thisResourceDir)
			repo.remote().pull()
		else:
			os.makedirs(thisResourceDir)
			git.Repo.clone_from(resourceInfo["url"], thisResourceDir)
		return thisResourceDir
	elif resourceInfo['type'] == 'dir':
		assert isinstance(resourceInfo['url'], six.string_types) or isinstance(resourceInfo['url'],list), 'The URL for a dir resource must be a single or multiple addresses'
		if isinstance(resourceInfo['url'], six.string_types):
			urls = [resourceInfo['url']]
		else:
			urls = resourceInfo['url']
		
		if 'filter' in resourceInfo:
			fileSuffixFilter = resourceInfo['filter']
		else:
			fileSuffixFilter = None

		if not os.path.isdir(thisResourceDir):
			print("  Creating directory...")
			os.makedirs(thisResourceDir)

		print("  Starting download...")
		for url in urls:
			basename = url.split('/')[-1]
			assert isinstance(url,six.string_types), 'Each URL for the dir resource must be a string'
			download(url,os.path.join(thisResourceDir,basename),fileSuffixFilter)

		if 'unzip' in resourceInfo and resourceInfo['unzip'] == True:
			print("  Unzipping archives...")
			for filename in os.listdir(thisResourceDir):
				if filename.endswith('.tar.gz') or filename.endswith('.tgz'):
					tar = tarfile.open(os.path.join(thisResourceDir,filename), "r:gz")
					tar.extractall(thisResourceDir)
					tar.close()
				elif filename.endswith('.gz'):
					unzippedName = filename[:-3]
					gunzip(os.path.join(thisResourceDir,filename), os.path.join(thisResourceDir,unzippedName), deleteSource=True)

		if not fileSuffixFilter is None:
			print("  Removing files not matching filter (%s)..." % fileSuffixFilter)
			for root, subdirs, files in os.walk(thisResourceDir):
				for f in files:
					if not f.endswith(fileSuffixFilter):
						fullpath = os.path.join(root,f)
						os.unlink(fullpath)

		if 'generatePubmedHashes' in resourceInfo and resourceInfo['generatePubmedHashes'] == True:
			print("  Generating Pubmed hashes...")
			hashDir = os.path.join(resourceDir,resource+'.hashes')
			if not os.path.isdir(hashDir):
				os.makedirs(hashDir)

			snakefile = thisResourceDir + ".hashes.SnakeFile"
			generatePubmedHashes(thisResourceDir,hashDir)

		return thisResourceDir
	else:
		raise RuntimeError("Unknown resource type (%s) for resource: %s" % (resourceInfo['type'],resource))

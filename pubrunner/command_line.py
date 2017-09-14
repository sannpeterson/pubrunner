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

def execCommands(commands):
	assert isinstance(commands,list)
	for command in commands:
		print(command)
		subprocess.call(shlex.split(command))

class ResourceStatus:
	class Base: pass
	class NO_CHANGE(Base): pass
	class INCREMENTAL_CHANGE(Base): pass
	class COMPLETE_CHANGE(Base): pass
	#NO_CHANGE = 1
	#INCREMENTAL_CHANGE = 2
	#COMPLETE_CHANGE = 3

def calcSHA256(filename):
	return hashlib.sha256(open(filename, 'rb').read()).hexdigest()

def calcSHA256forDir(directory):
	sha256s = {}
	for filename in os.listdir(directory):
		sha256 = calcSHA256(os.path.join(directory,filename))
		sha256s[filename] = sha256
	return sha256s

def ftpDirListing(f):
	try:
		tmp = []
		f.retrlines('MLSD', tmp.append)
		listings = {}
		for t in tmp:
			split = t.split(';')
			name = split[-1].strip()
			attributes = [ tuple(a.split('=')) for a in split[0:-1] ]
			listing = { a:b for a,b in attributes }
			listings[name] = listing
	except ftplib.error_perm, resp:
		if str(resp) == "550 No files found":
			listings = []
		else:
			raise

	return listings

def ftpIsDir(url):
	url = url.replace("ftp://","")
	root = url.split('/')[0]
	parent = "/".join(url.split('/')[1:-1])
	basename = url.split('/')[-1]
	ftp = ftplib.FTP(root)
	ftp.login("anonymous", "ftplib")
	ftp.cwd(parent)
	listings = ftpDirListing(ftp)
	thingType = listings[basename]['type']
	assert thingType == 'file' or thingType == 'dir'
	return thingType == 'dir'

def download(url,out):
	if url.startswith('ftp'):
		#isDir = ftpIsDir(url)

		#hostname = url.replace("ftp://","").
		url = url.replace("ftp://","")
		hostname = url.split('/')[0]
		#parent = "/".join(url.split('/')[1:-1])
		path = "/".join(url.split('/')[1:])
		#basename = url.split('/')[-1]


		with ftputil.FTPHost(hostname, 'anonymous', 'secret') as host:
			isDir = host.path.isdir(path)
			isFile = host.path.isfile(path)
			assert isDir or isFile

			if isDir:
				assert os.path.isdir(out), "FTP path (%s) is a directory. Expect a directory as output" % url
				for filename in host.listdir(path):
					host.download(filename,os.path.join(out,filename))
			else:
				host.download(path,out)
		#if isDir:
		#	if not os.path.isdir(out):
		#		raise RuntimeError("FTP path (%s) is a directory. Expect a directory as output" % url)
			
			

		
		#ftpDirListing(url)

		#sys.exit(0)
	else:
		wget.download(url,out,bar=None)

def gunzip(source,dest,deleteSource=False):
	with gzip.open(source, 'rb') as f_in, open(dest, 'wb') as f_out:
		shutil.copyfileobj(f_in, f_out)

	if deleteSource:
		os.unlink(source)

def getResource(resource):
	print("Fetching resource: %s" % resource)

	#homeDir = os.path.expanduser("~")
	homeDir = '/projects/bioracle/jake/pubrunnerTmp'
	baseDir = os.path.join(homeDir,'.pubrunner')
	thisResourceDir = os.path.join(baseDir,'resources',resource)

	packagePath = os.path.dirname(pubrunner.__file__)
	resourceYamlPath = os.path.join(packagePath,'resources','%s.yml' % resource)
	assert os.path.isfile(resourceYamlPath), "Can not find appropriate file for resource: %s" % resource

	with open(resourceYamlPath) as f:
		resourceInfo = yaml.load(f)

	print(json.dumps(resourceInfo,indent=2))

	if resourceInfo['type'] == 'git':
		assert isinstance(resourceInfo['url'], six.string_types), 'The URL for a git resource must be a single address'

		if os.path.isdir(thisResourceDir):
			# Assume it is an existing git repo
			repo = git.Repo(thisResourceDir)
			beforeHash = str(repo.heads.master.commit)
			repo.remote().pull()
			afterHash = str(repo.heads.master.commit)
			if beforeHash == afterHash:
				change = ResourceStatus.NO_CHANGE
			else:
				change = ResourceStatus.COMPLETE_CHANGE
		else:
			os.makedirs(thisResourceDir)
			git.Repo.clone_from(resourceInfo["url"], thisResourceDir)
			change = ResourceStatus.COMPLETE_CHANGE
		return (thisResourceDir,change)
	elif resourceInfo['type'] == 'dir':
		assert isinstance(resourceInfo['url'], six.string_types) or isinstance(resourceInfo['url'],list), 'The URL for a dir resource must be a single or multiple addresses'
		if isinstance(resourceInfo['url'], six.string_types):
			urls = [resourceInfo['url']]
		else:
		 	urls = resourceInfo['url']

		if os.path.isdir(thisResourceDir):
			beforeHash = calcSHA256forDir(thisResourceDir)
			for url in urls:
				basename = url.split('/')[-1]
				assert isinstance(url,six.string_types), 'Each URL for the dir resource must be a string'
				download(url,os.path.join(thisResourceDir,basename))
				
			afterHash = calcSHA256forDir(thisResourceDir)
			if beforeHash == afterHash:
				change = ResourceStatus.NO_CHANGE
			else:
				change = ResourceStatus.COMPLETE_CHANGE
		else:
		 	os.makedirs(thisResourceDir)
			for url in urls:
				basename = url.split('/')[-1]
				assert isinstance(url,six.string_types), 'Each URL for the dir resource must be a string'
				download(url,os.path.join(thisResourceDir,basename))
		 	change = ResourceStatus.COMPLETE_CHANGE
		
		if resourceInfo['unzip'] == True:
			for filename in os.listdir(thisResourceDir):
				if filename.endswith('.gz'):
					unzippedName = filename[:-3]
					gunzip(os.path.join(thisResourceDir,filename), os.path.join(thisResourceDir,unzippedName), deleteSource=True)


		return (thisResourceDir,change)
	else:
		raise RuntimeError("Unknown resource type (%s) for resource: %s" % (resourceInfo['type'],resource))

#	if dataset == "PUBMED_SINGLEFILE":
#		datasetDir = os.path.join(baseDir,dataset)
#		if not os.path.isdir(datasetDir):
#			os.makedirs(datasetDir)
#
#		singleFile = 'ftp://ftp.ncbi.nlm.nih.gov/pubmed/baseline/medline17n0892.xml.gz'
#		wget.download(singleFile,datasetDir)
#		fileGZ = os.path.join(datasetDir,'medline17n0892.xml.gz')
#		fileXML = os.path.join(datasetDir,'medline17n0892.xml')
#
#		with gzip.open(fileGZ, 'rb') as f_in, open(fileXML, 'wb') as f_out:
#			shutil.copyfileobj(f_in, f_out)
#
#		return datasetDir
#	else:
#		raise RuntimeError("Unknown dataset: %s" % dataset)

def loadYAML(yamlFilename):
	yamlData = None
	with open(yamlFilename,'r') as f:
		try:
			yamlData = yaml.load(f)
		except yaml.YAMLError as exc:
			print(exc)
			raise
	return yamlData

def findSettingsFile():
	possibilities = [ os.getcwd(), os.path.expanduser("~") ]
	for directory in possibilities:
		settingsPath = os.path.join(directory,'.pubrunner.settings.yml')
		if os.path.isfile(settingsPath):
			return settingsPath
	raise RuntimeError("Unable to find .pubrunner.settings.yml file. Tried current directory first, then home directory")
	
def pubrun(directory,doTest):
	settingsYamlFile = findSettingsFile()
	globalSettings = loadYAML(settingsYamlFile)

	os.chdir(directory)

	toolYamlFile = '.pubrunner.yml'
	if not os.path.isfile(toolYamlFile):
		raise RuntimeError("Expected a .pubrunner.yml file in root of codebase")

	toolSettings = loadYAML(toolYamlFile)

	if "build" in toolSettings:
		print("Running build")
		execCommands(toolSettings["build"])
	
	print("Fetching datasets")
	datasets = toolSettings["testdata"] if doTest else toolSettings["rundata"]
	datasetMap = {}
	for dataset in datasets:
		datasetMap[dataset] = fetchDataset(dataset)

	print("Running tool")
	outputDir = tempfile.mkdtemp()
	runCommands = toolSettings["test"] if doTest else toolSettings["run"]
	print(runCommands)
	adaptedCommands = []
	for command in runCommands:
		split = command.split(' ')
		for i in range(len(split)):
			if split[i] in datasetMap:
				split[i] = datasetMap[split[i]]
			elif split[i] == 'OUTPUTDIR':
				split[i] = outputDir
			elif split[i] == 'OUTPUTFILE':
				split[i] = os.path.join(outputDir,'output')
		adaptedCommand = " ".join(split)
		adaptedCommands.append(adaptedCommand)
	print(adaptedCommands)
	execCommands(adaptedCommands)


	if "upload" in globalSettings:
		print(json.dumps(globalSettings,indent=2))
		if "ftp" in globalSettings["upload"]:
			print("Uploading results to FTP")
			pubrunner.pushToFTP(outputDir,toolSettings,globalSettings)
		if "local-directory" in globalSettings["upload"]:
			print("Uploading results to local directory")
			pubrunner.pushToLocalDirectory(outputDir,toolSettings,globalSettings)
		if "zenodo" in globalSettings["upload"]:
			print("Uploading results to Zenodo")
			pubrunner.pushToZenodo(outputDir,toolSettings,globalSettings)

	print("Sending update to website")

def cloneGithubRepoToTempDir(githubRepo):
	tempDir = tempfile.mkdtemp()
	Repo.clone_from(githubRepo, tempDir)
	return tempDir

def main():
	parser = argparse.ArgumentParser(description='PubRunner will manage the download of needed resources for a text mining tool, build and execute it and then share the results publicly')
	parser.add_argument('codebase',nargs='?',type=str,help='Code base containing the text mining tool to execute. Code base should contain a .pubrunner.yml file. The code base can be a directory, Github repo or archive')
	parser.add_argument('--test',action='store_true',help='Run the test functionality instead of the full run')
	parser.add_argument('--getResource',required=False,type=str,help='Fetch a specific resource (instead of doing a normal PubRunner run). This is really only needed for debugging and understanding resources.')

	args = parser.parse_args()

	if args.getResource:
		location = getResource(args.getResource)
		print("Downloaded latest version of resource '%s' to location:" % args.getResource)
		print(location)
		print("")
		print("Exiting without doing PubRun")
		sys.exit(0)
	
	if not args.codebase:
		print("codebase must be provided (if not downloading individual resources)")
		parser.print_help()
		sys.exit(1)

	if os.path.isdir(args.codebase):
		pubrun(args.codebase,args.test)
	elif args.codebase.startswith('https://github.com/'):
		tempDir = ''
		try:
			tempDir = cloneGithubRepoToTempDir(args.codebase)
			pubrun(tempDir,args.test)
			shutil.rmtree(tempDir)
		except:
			if os.path.isdir(tempDir):
				shutil.rmtree(tempDir)
			logging.error(traceback.format_exc())
			raise

	elif os.path.isfile(args.codebase):
		raise RuntimeError("Not implemented")
	else:
		raise RuntimeError("Not sure what to do with codebase: %s. Doesn't appear to be a directory, Github repo or archive")



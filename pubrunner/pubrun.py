import pubrunner
import os
import shutil
import yaml
import json
import six
import re
import requests
import datetime
import csv
import atexit
import codecs
from collections import defaultdict
from Bio import Entrez

def extractVariables(command):
	assert isinstance(command,six.string_types)
	#regex = re.compile("\?[A-Za-z_0-9]*")
	regex = re.compile("\{\S*\}")
	variables = []
	for m in regex.finditer(command):
		var = ( m.start(), m.end(), m.group()[1:-1] )
		variables.append(var)
	variables = sorted(variables,reverse=True)
	return variables


def getResourceLocation(resource):
	globalSettings = pubrunner.getGlobalSettings()
	resourceDir = os.path.expanduser(globalSettings["storage"]["resources"])
	thisResourceDir = os.path.join(resourceDir,resource)
	return thisResourceDir

def eutilsToFile(db,id,filename):
	Entrez.email = "jlever@bcgsc.ca"     # Always tell NCBI who you are
	handle = Entrez.efetch(db=db, id=id, rettype="gb", retmode="xml")
	with codecs.open(filename,'w','utf-8') as f:
		xml = handle.read()
		f.write(xml)

def preprocessResourceSettings(toolSettings):
	for resourceGroupName in toolSettings["resources"]:
		newResources = []
		for resource in toolSettings["resources"][resourceGroupName]:
			if isinstance(resource,dict):
				assert len(resource.items()) == 1, "ERROR in pubrunner.yml: A resource (%s) is not being parsed correctly. It is likely that the resource settings (e.g. format) are not indented properly. Try indenting more" % (str(list(resource.keys())[0]))
				resource,projectSettings = list(resource.items())[0]
				newResource = ( resource, projectSettings)
			else:
				newResource = ( resource, {} )

			newResources.append(newResource)
		toolSettings["resources"][resourceGroupName] = newResources

def prepareConversionAndHashingRuns(toolSettings,mode,workingDirectory):
	newResourceList = []
	conversions = []
	resourcesWithHashes = []
	for resourceGroupName in ["all",mode]:
		for resName,projectSettings in toolSettings["resources"][resourceGroupName]:
			allowed = ['rename','format','removePMCOADuplicates','usePubmedHashes','pmids','pmcids']
			for k in projectSettings.keys():
				assert k in allowed, "Unexpected attribute (%s) for resource %s" % (k,resName)

			nameToUse = resName
			if "rename" in projectSettings:
				nameToUse = projectSettings["rename"]

			if resName == 'PUBMED_CUSTOM':
				#print(projectSettings)
				if "format" in projectSettings:
					dirToCreate = nameToUse + "_UNCONVERTED"
				else:
					dirToCreate = nameToUse
				dirToCreate = os.path.join(workingDirectory,dirToCreate)
				if os.path.isdir(dirToCreate):
					shutil.rmtree(dirToCreate)
				os.makedirs(dirToCreate)

				pmids = str(projectSettings['pmids'])
				for pmid in pmids.split(','):
					filename = os.path.join(dirToCreate,'%d.xml' % int(pmid))
					eutilsToFile('pubmed',pmid,filename)

				resInfo = {'format':'pubmedxml','chunkSize':1}
			elif resName == 'PMCOA_CUSTOM':
				#print(projectSettings)
				if "format" in projectSettings:
					dirToCreate = nameToUse + "_UNCONVERTED"
				else:
					dirToCreate = nameToUse
				dirToCreate = os.path.join(workingDirectory,dirToCreate)
				if os.path.isdir(dirToCreate):
					shutil.rmtree(dirToCreate)
				os.makedirs(dirToCreate)

				pmcids = str(projectSettings['pmcids'])
				for pmcid in pmcids.split(','):
					filename = os.path.join(dirToCreate,'%d.nxml' % int(pmcid))
					eutilsToFile('pmc',pmcid,filename)

				resInfo = {'format':'pmcxml','chunkSize':1}
			else:
				resInfo = pubrunner.getResourceInfo(resName)

			if "format" in projectSettings:
				inDir = nameToUse + "_UNCONVERTED"
				inFormat = resInfo["format"]

				if 'chunkSize' in resInfo:
					chunkSize = resInfo["chunkSize"]
				else:
					chunkSize = 1

				outDir = nameToUse
				outFormat = projectSettings["format"]

				removePMCOADuplicates = False
				if "removePMCOADuplicates" in projectSettings and projectSettings["removePMCOADuplicates"] == True:
					removePMCOADuplicates = True

				#command = "pubrunner_convert --i {IN:%s/*%s} --iFormat %s --o {OUT:%s/*%s} --oFormat %s" % (inDir,inFilter,inFormat,outDir,inFilter,outFormat)
				conversionInfo = (os.path.join(workingDirectory,inDir),inFormat,os.path.join(workingDirectory,outDir),outFormat,chunkSize)
				conversionInfo = {}
				conversionInfo['inDir'] = os.path.join(workingDirectory,inDir)
				conversionInfo['inFormat'] = inFormat
				conversionInfo['outDir'] = os.path.join(workingDirectory,outDir)
				conversionInfo['outFormat'] = outFormat
				conversionInfo['chunkSize'] = chunkSize
				conversions.append( conversionInfo )

				whichHashes = None
				if "usePubmedHashes" in projectSettings:
					whichHashes = [ p.strip() for p in projectSettings["usePubmedHashes"].split(',') ]

				resourceSymlink = os.path.join(workingDirectory,inDir)
				if not os.path.islink(resourceSymlink) and not os.path.isdir(resourceSymlink):
					os.symlink(getResourceLocation(resName), resourceSymlink)

				if "generatePubmedHashes" in resInfo and resInfo["generatePubmedHashes"] == True:
					hashesSymlink = os.path.join(workingDirectory,inDir+'.hashes')
					hashesInfo = {'resourceDir':os.path.join(workingDirectory,inDir),'hashDir':hashesSymlink,'removePMCOADuplicates':removePMCOADuplicates,'whichHashes':whichHashes}

					resourcesWithHashes.append(hashesInfo)
					if not os.path.islink(hashesSymlink):
						hashesDir = getResourceLocation(resName)+'.hashes'
						#assert os.path.isdir(hashesDir), "Couldn't find directory containing hashes for resource: %s. Looked in %s" % (resName,hashesDir)
						os.symlink(hashesDir, hashesSymlink)

				newDirectory = os.path.join(workingDirectory,outDir)
				if not os.path.isdir(newDirectory):
					os.makedirs(newDirectory)
			else:
				resourceSymlink = os.path.join(workingDirectory,nameToUse)
				if not os.path.islink(resourceSymlink) and not os.path.isdir(resourceSymlink):
					os.symlink(getResourceLocation(resName), resourceSymlink)

	toolSettings["pubmed_hashes"] = resourcesWithHashes

	toolSettings["conversions"] = conversions

def cleanWorkingDirectory(directory,doTest,execute=False):
	mode = "test" if doTest else "full"

	globalSettings = pubrunner.getGlobalSettings()
	os.chdir(directory)

	toolYamlFile = 'pubrunner.yml'
	if not os.path.isfile(toolYamlFile):
		raise RuntimeError("Expected a %s file in root of codebase" % toolYamlFile)

	toolSettings = pubrunner.loadYAML(toolYamlFile)
	toolName = toolSettings["name"]

	workspaceDir = os.path.expanduser(globalSettings["storage"]["workspace"])
	workingDirectory = os.path.join(workspaceDir,toolName,mode)

	if os.path.isdir(workingDirectory):
		print("Removing working directory for tool %s" % toolName)
		print("Directory: %s" % workingDirectory)
		shutil.rmtree(workingDirectory)
	else:
		print("No working directory to remove for tool %s" % toolName)
		print("Expected directory: %s" % workingDirectory)
		
def downloadPMCOAMetadata(workingDirectory):
	url = 'ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_file_list.csv'
	localFile = os.path.join(workingDirectory,'oa_file_list.csv')
	pubrunner.download(url,localFile)

	pmids = set()
	pmcidsToLastUpdate = defaultdict(lambda : "")
	with open(localFile) as csvfile:
		reader = csv.DictReader(csvfile)
		for row in reader:
			pmid = row['PMID']
			pmcid = row['Accession ID']
			lastupdated = row['Last Updated (YYYY-MM-DD HH:MM:SS)']
			if pmcid != '':
				pmcidsToLastUpdate[pmcid] = lastupdated
			if pmid != '':
				pmids.add(int(pmid))

	os.unlink(localFile)

	return pmids,pmcidsToLastUpdate

def cleanup():
	if os.path.isdir('.pubrunner_lock'):
		shutil.rmtree('.pubrunner_lock')
	if os.path.isdir('.snakemake'):
		shutil.rmtree('.snakemake')

# https://stackoverflow.com/questions/312443/how-do-you-split-a-list-into-evenly-sized-chunks
def chunks(l, n):
	"""Yield successive n-sized chunks from l."""
	for i in range(0, len(l), n):
		yield l[i:i + n]

def findFiles(dirName):
	allFiles = []
	for root, dirs, files in os.walk(dirName):
		allFiles += [ os.path.join(root,f) for f in files ]
	
	# We're going to extract the last set of digits from each filename and sort by that
	nums = [ re.findall('[0-9]+',f) for f in allFiles ]
	nums = [ 0 if num == [] else int(num[-1]) for num in nums ]
	sortedByNum = sorted(list(zip(nums,allFiles)))
	sortedFilepaths = [ filepath for num,filepath in sortedByNum ]
	
	return sortedFilepaths

class OutputFileNamer:
	def __init__(self,directory,fileFormat):
		self.directory = directory
		self.fileFormat = fileFormat
		self.i = 0

	def next(self):
		for _ in range(10000):
			outputFile = os.path.join(self.directory,self.fileFormat % self.i)
			self.i += 1
			if not os.path.isfile(outputFile):
				return outputFile
		raise RuntimeError("Unable to create an output file that doesn't already exist")

def getPMCIDFromFilename(filename):
	pmcidSearch = re.search('PMC\d+',filename)
	if pmcidSearch:
		return pmcidSearch.group()
	else:
		return None

def assignFilesForConversion(inDir, previousAssignmentFile, outDir, outPattern, maxChunkSize, pmcidsToLastUpdate=None):
	files = findFiles(inDir)

	if not pmcidsToLastUpdate is None:
		print("Sorting files by PMC last update metadata")
		filesWithUpdates = [ (pmcidsToLastUpdate[getPMCIDFromFilename(f)],f) for f in files ]
		atleastOneUpdate = any (lastupdate != '' for lastupdate,f in filesWithUpdates )
		assert atleastOneUpdate, "No update dates associated with PMCIDs. Must have been a problem loading the file"
		filesWithUpdates = sorted(filesWithUpdates)
		files = [ f for lastupdate,f in filesWithUpdates ]

	assignedChunks = previousAssignmentFile

	# We'll check if any previous input files have disappeared, and set that chunk to dirty (so it is reprocessed)
	filesSet = set(files)
	missingFiles = [ f for f in assignedChunks.keys() if not f in filesSet ]
	dirtyOutputFiles = set( [ assignedChunks[f] for f in missingFiles ] )
	for f in missingFiles:
		del assignedChunks[f]

	if len(assignedChunks) > 0:
		# We're just take the last chunk alphabetically
		currentChunk = sorted(assignedChunks.values())[-1]
		currentChunkSize = len( [ f for f in assignedChunks.values() if f == currentChunk ] )
	else:
		currentChunk = None
		currentChunkSize = 0

	outputFileNamer = OutputFileNamer(outDir,outPattern)

	for f in files:
		if not f in assignedChunks:
			if currentChunk is None or currentChunkSize >= maxChunkSize:
				currentChunk = outputFileNamer.next()
				currentChunkSize = 0
			
			assignedChunks[f] = currentChunk
			dirtyOutputFiles.add(currentChunk)
			currentChunkSize += 1

	# Remove any dirty files to force them to be recalculated
	for dirtyOutputFile in dirtyOutputFiles:
		if os.path.isfile(dirtyOutputFile):
			os.unlink(dirtyOutputFile)
			print("Removing:", dirtyOutputFile)

	outputFilesWithChunks = defaultdict(list)
	for f,outputFile in assignedChunks.items():
		outputFilesWithChunks[outputFile].append(f)

	return outputFilesWithChunks

def pubrun(directory,doTest,doGetResources,forceresource_dir=None,forceresource_format=None,outputdir=None):
	mode = "test" if doTest else "full"

	globalSettings = pubrunner.getGlobalSettings()

	os.chdir(directory)
	
	if os.path.isdir('.pubrunner_lock'):
		raise RuntimeError("A .pubrunner_lock directory exists in this project directory. These are created by PubRunner during an incomplete run. Are you sure another instance of PubRunner is not currently running? If you're sure, you will need to delete this directory before continuing. The directory is: %s" % os.path.join(directory,'.pubrunner_lock'))

	os.mkdir('.pubrunner_lock')
	atexit.register(cleanup)

	toolYamlFile = 'pubrunner.yml'
	if not os.path.isfile(toolYamlFile):
		raise RuntimeError("Expected a %s file in root of codebase" % toolYamlFile)

	toolSettings = pubrunner.loadYAML(toolYamlFile)
	toolName = toolSettings["name"]

	workspacesDir = os.path.expanduser(globalSettings["storage"]["workspace"])
	workingDirectory = os.path.join(workspacesDir,toolName,mode)
	if not os.path.isdir(workingDirectory):
		os.makedirs(workingDirectory)

	print("Working directory: %s" % workingDirectory)
	
	if not "build" in toolSettings:
		toolSettings["build"] = []
	if not "all" in toolSettings["resources"]:
		toolSettings["resources"]["all"] = []
	if not mode in toolSettings["resources"]:
		toolSettings["resources"][mode] = []

	preprocessResourceSettings(toolSettings)

	resourcesInUse = toolSettings["resources"]['all'] + toolSettings["resources"][mode]
	if not forceresource_dir is None:
		assert os.path.isdir(forceresource_dir), "forceresource_dir must be a directory. %s is not" % forceresource_dir
		if len(resourcesInUse) > 0:
			firstResourceName,_ = resourcesInUse[0]
			print("\nUsing provided resource location for first resource %s" % firstResourceName)

			singleConversion = {}
			singleConversion["inDir"] = forceresource_dir
			singleConversion["inFormat"] = forceresource_format
			singleConversion["outDir"] = toolSettings["conversions"][0]["outDir"]
			singleConversion["outFormat"] = toolSettings["conversions"][0]["outFormat"]
			singleConversion["chunkSize"] = 1
			for conversion in toolSettings["conversions"][1:]:
				# Remove the symlink to the normal resource and create an empty directory instead (and remove the conversion for this resource)
				os.unlink(conversion["inDir"])
				shutil.rmtree(conversion["outDir"])
				os.makedirs(conversion["outDir"])
			if len(resourcesInUse) > 1:
				otherResources = [ resName for resName,_ in resourcesInUse[1:] ]
				print("Using empty directories for remaining resources: %s" % ",".join(otherResources))
			toolSettings["conversions"] = [singleConversion]
			toolSettings["pubmed_hashes"] = []
		#print(json.dumps(toolSettings,indent=2))
		#sys.exit(0)
	elif doGetResources:
		print("\nGetting resources")
		for resName,_ in resourcesInUse:
			if resName in ['PUBMED_CUSTOM','PMCOA_CUSTOM']:
				continue
			pubrunner.getResource(resName)
	else:
		print("\nNot getting resources (--nogetresource)")

	prepareConversionAndHashingRuns(toolSettings,mode,workingDirectory)

	pmidsFromPMCFile,pmcidsToLastUpdate = None,None
	needPMIDsFromPMC = any( hashesInfo['removePMCOADuplicates'] for hashesInfo in toolSettings["pubmed_hashes"] )
	pmcoaIsAResource = any( resName == 'PMCOA' for resName,_ in resourcesInUse )
	if needPMIDsFromPMC or pmcoaIsAResource:
		print("\nGetting Pubmed Central metadata for PMID info and/or file dates")
		pmidsFromPMCFile,pmcidsToLastUpdate = downloadPMCOAMetadata(workingDirectory)

	directoriesWithHashes = set()
	if toolSettings["pubmed_hashes"] != []:
		print("\nUsing Pubmed Hashes to identify updates")
		for hashesInfo in toolSettings["pubmed_hashes"]:
			hashDirectory = hashesInfo['hashDir']
			whichHashes = hashesInfo['whichHashes']
			removePMCOADuplicates = hashesInfo['removePMCOADuplicates']

			directoriesWithHashes.add(hashesInfo['resourceDir'])

			pmidDirectory = hashesInfo["resourceDir"].rstrip('/') + '.pmids'
			print("Using hashes in %s to identify PMID updates" % hashDirectory)
			if removePMCOADuplicates:
				assert not pmidsFromPMCFile is None
				pubrunner.gatherPMIDs(hashDirectory,pmidDirectory,whichHashes=whichHashes,pmidExclusions=pmidsFromPMCFile)
			else:
				pubrunner.gatherPMIDs(hashDirectory,pmidDirectory,whichHashes=whichHashes)

	print("\nRunning conversions")
	for conversionInfo in toolSettings["conversions"]:
		inDir,inFormat = conversionInfo['inDir'],conversionInfo['inFormat']
		outDir,outFormat = conversionInfo['outDir'],conversionInfo['outFormat']
		chunkSize = conversionInfo['chunkSize']

		chunksFile = outDir + '.json'
		previousChunks = {}
		if os.path.isfile(chunksFile):
			with open(chunksFile,'wb') as f:
				previousChunks = json.load(f)

		outPattern = os.path.basename(inDir) + ".converted.%08d." + outFormat
		if  os.path.basename(inDir) == 'PMCOA_UNCONVERTED':
			newChunks = assignFilesForConversion(inDir, previousChunks, outDir, outPattern, chunkSize, pmcidsToLastUpdate)
		else:
			newChunks = assignFilesForConversion(inDir, previousChunks, outDir, outPattern, chunkSize)

		with open(chunksFile,'w') as f:
			json.dump(newChunks,f,indent=2)

		parameters = {'CHUNKS':chunksFile,'INFORMAT':inFormat,'OUTFORMAT':outFormat,'CHUNKSIZE':str(chunkSize)}
		if inDir in directoriesWithHashes:
			pmidDirectory = inDir.rstrip('/') + '.pmids'
			assert os.path.isdir(pmidDirectory), "Cannot find PMIDs directory for resource. Tried: %s" % pmidDirectory
			parameters['PMIDDIR'] = pmidDirectory

		convertSnakeFile = os.path.join(pubrunner.__path__[0],'Snakefiles','Convert.py')
		pubrunner.launchSnakemake(convertSnakeFile,parameters=parameters)

	runSnakeFile = os.path.join(pubrunner.__path__[0],'Snakefiles','Run.py')
	for commandGroup in ["build","run"]:
		for i,command in enumerate(toolSettings[commandGroup]):
			print("\nStarting '%s' command #%d: %s" % (commandGroup,i+1,command))
			useClusterIfPossible = True
			parameters = {'COMMAND':command,'DATADIR':workingDirectory}
			pubrunner.launchSnakemake(runSnakeFile,useCluster=useClusterIfPossible,parameters=parameters)
			print("")

	if "output" in toolSettings:
		outputList = toolSettings["output"]
		if not isinstance(outputList,list):
			outputList = [outputList]

		outputLocList = [ os.path.join(workingDirectory,o) for o in outputList ]

		print("\nExecution of tool is complete. Full paths of output files are below:")
		for f in outputLocList:
			print('  %s' % f)
		print()

		if not outputdir is None:
			print("\nCopying results to output directory: %s" % outputdir)
			if not os.path.isdir(outputdir):
				os.makedirs(outputdir)
			for o in outputList:
				fromFile = os.path.join(workingDirectory,o)
				toFile = os.path.join(outputdir,o)
				shutil.copy(fromFile,toFile)

		if mode != 'test':

			dataurl = None
			if "upload" in globalSettings:
				if "ftp" in globalSettings["upload"]:
					print("Uploading results to FTP")
					pubrunner.pushToFTP(outputLocList,toolSettings,globalSettings)
				if "local-directory" in globalSettings["upload"]:
					print("Uploading results to local directory")
					pubrunner.pushToLocalDirectory(outputLocList,toolSettings,globalSettings)
				if "zenodo" in globalSettings["upload"]:
					print("Uploading results to Zenodo")
					dataurl = pubrunner.pushToZenodo(outputLocList,toolSettings,globalSettings)

			if "website-update" in globalSettings and toolName in globalSettings["website-update"]:
				assert not dataurl is None, "Don't have URL to update website with"
				websiteToken = globalSettings["website-update"][toolName]
				print("Sending update to website")
				
				headers = {'User-Agent': 'Pubrunner Agent', 'From': 'no-reply@pubrunner.org'  }
				today = datetime.datetime.now().strftime("%m-%d-%Y")	
				updateData = [{'authentication':websiteToken,'success':True,'lastRun':today,'codeurl':toolSettings['url'],'dataurl':dataurl}]
				
				jsonData = json.dumps(updateData)
				r = requests.post('http://www.pubrunner.org/update.php',headers=headers,files={'jsonFile': jsonData})
				assert r.status_code == 200, "Error updating website with job status"
			else:
				print("Could not update website. Did not find %s under website-update in .pubrunner.settings.yml file" % toolName)




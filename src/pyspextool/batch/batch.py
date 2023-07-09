# Functions for batch reduction using pyspextool
# General procedure:
# - read in files in a given folder and process into a log file and batch driving file
#  -- subprocesses: files --> database, database --> log, database --> batch driver
# - user checks logs and batch driving file to ensure they are correct
# - run through reduction steps based on batch driving file
#  -- subprocesses: read driver --> do all calibrations --> extract all spectra 
#		--> combine spectral groups --> telluric correction --> stitch (SXD/LXD)
#
# REMAINING TASKS
# - generate final source lists (fixed vs moving) with simbad/2mass information
# - test individual extraction options
# - xtellcor integration
# - stitching integration
# - finish docstrings
# - integrate testing scripts
# - TESTING

from astropy.io import fits
from astropy.coordinates import SkyCoord
from astropy.time import Time
import astropy.units as u
import copy
import os
import glob
import pandas as pd
import numpy as np
import shutil
import sys
import yaml

import pyspextool as ps
from pyspextool import config as setup
from pyspextool.io.files import extract_filestring,make_full_path

ERROR_CHECKING = True
DIR = os.path.dirname(os.path.abspath(__file__))

MOVING_MAXSEP = 15 # max separation for fixed target in arcsec
MOVING_MAXRATE = 10 # max moving rate for fixed target in arcsec/hr
INSTRUMENT_DATE_SHIFT = Time('2014-07-01').mjd # date spex --> uspex
ARC_NAME = 'arc lamp'
FLAT_NAME = 'flat lamp'
OBSERVATION_SET_KEYWORD = 'OBS_SET'
DRIVER_DEFAULT_FILENAME = 'driver.txt'

# this is the data to extract from header, with options for header keywords depending on when file was written
HEADER_DATA={
	'UT_DATE': ['DATE_OBS'],
	'UT_TIME': ['TIME_OBS'],
#	'MJD': ['MJD_OBS'],
	'TARGET_NAME': ['OBJECT','TCS_OBJ'],
	'RA': ['RA','TCS_RA'],
	'DEC': ['DEC','TCS_DEC'],
	'HA': ['HA','TCS_HA'],
	'PA': ['POSANGLE'],
	'AIRMASS': ['AIRMASS','TCS_AM'],
	'INTEGRATION': ['ITIME'],
	'COADDS': ['CO_ADDS'],
	'SLIT': ['SLIT'],
	'MODE': ['GRAT'],
	'BEAM': ['BEAM'],
	'PARALLACTIC': ['TCS_PA'],
	'PROGRAM': ['PROG_ID'],
	'OBSERVER': ['OBSERVER'],
	'TARGET_TYPE': ['DATATYPE'],
}

# these are default parameters for generating the log
LOG_PARAMETERS = {
	'FILENAME': 'log.csv',
	'HTML_TEMPLATE_FILE' : os.path.join(DIR,'log_template.txt'),
	'BASE_HTML': r'<!DOCTYPE html>\n<html lang="en">\n\n<head>\n<title>SpeX Log</title>\n</head>\n\n<body>\n<h1>SpeX Log</h1>\n\n[TABLE]\n\n</body>\n</html>',	
	'COLUMNS' : ['FILE','BEAM','TARGET_NAME','TARGET_TYPE','FIXED-MOVING','RA','DEC','UT_DATE','UT_TIME','MJD','AIRMASS','INTEGRATION','COADDS','INSTRUMENT','SLIT','MODE','PROGRAM','OBSERVER'],
}

# these are default batch reduction parameters
BATCH_PARAMETERS = {
	'INSTRUMENT': 'spex',
	'DATA_FOLDER': '',
	'CALS_FOLDER': '',
	'PROC_FOLDER': '',
	'QA_FOLDER': '',
	'FLAT_FILE_PREFIX': 'flat',
	'ARC_FILE_PREFIX': 'arc',
	'SCIENCE_FILE_PREFIX': 'spc',
	'SPECTRA_FILE_PREFIX': 'spectra',
	'COMBINED_FILE_PREFIX': 'combspec',
	'CALIBRATED_FILE_PREFIX': 'calspec',
	'STITCHED_FILE_PREFIX': 'stitched',
	'PROGRAM': '',
	'OBSERVER': '',
	'QA_FILE': True,
	'QA_PLOT': False, 
	'PLOT_TYPE': '.pdf', 
}

# these are required parameters for target-calibration sequences
OBSERVATION_PARAMETERS_REQUIRED = {
	'MODE': 'SXD',
	'TARGET_TYPE': 'fixed ps',
	'TARGET_NAME': 'target',
	'TARGET_PREFIX': 'spc',
	'TARGET_FILES': '1-2',
	'FLAT_FILE': 'flat.fits',
	'WAVECAL_FILE': 'wavecal.fits',
	'STD_NAME': 'standard',
	'STD_PREFIX': 'spc',
	'STD_FILES': '1-2'
	}

# these are optional parameters for target-calibration sequences
OBSERVATION_PARAMETERS_OPTIONAL = {
#	'USE_STORED_SOLUTION': False,
	'ORDERS': '3-9',
#	'TARGET_TYPE': 'ps',
	'REDUCTION_MODE': 'A-B',
	'NPOSITIONS': 2,
	'APERTURE_POSITIONS': [3.7,11.2],
	'APERTURE_METHOD': 'auto',
	'APERTURE': 1.5,
	'PSF_RADIUS': 1.5,
	'BACKGROUND_RADIUS': 2.5,
	'BACKGROUND_WIDTH': 4,
	'SCALE_RANGE': [1.0,1.5],
}

# these are default parameters for QA page creation
QA_PARAMETERS = {
	'FILENAME': 'index.html',
	'TEMPLATE_FILE': os.path.join(DIR,'qa_template.txt'),
	'SOURCE_TEMPLATE_FILE': os.path.join(DIR,'qa_source_template.txt'),
	'SINGLE_PLOT_TEMPLATE_FILE': os.path.join(DIR,'qa_singleplot_template.txt'),
	'CSS_FILE' : os.path.join(DIR,'qa.css'),
	'PLOT_TYPE': '.pdf',
	'NIMAGES': 4,
	'IMAGE_WIDTH': 300,
	'MKWC_ARCHIVE_URL': 'http://mkwc.ifa.hawaii.edu/forecast/mko/archive/index.cgi',
	'HTML_TABLE_HEAD': '<table width=100%>\n <tr>\n',
	'HTML_TABLE_TAIL': ' </tr>\n</table>\n',
}



###############################
####### PROCESS FOLDERS #######
###############################

def process_folder(folder,verbose=False):
	'''
	Purpose
	-------
	Reads in fits files from a data folder and organizes into pandas dataframe 
	with select keywords from headers defined in HEADER_DATA

	Parameters
	----------
	folder : str
		full path to the data folder containing the fits files

	verbose : bool, default=False
		Set to True to return verbose output

	Outputs
	-------
	pandas DataFrame containing the main header elements of the fits data files

	Example
	-------
	>>> from pyspextool.batch import batch
	>>> dpath = '/Users/adam/data/spex/20200101/data/'
	>>> dp = batch.process_folder(dpath)
	>>> print(dp[:4])

		               FILE     UT_DATE          UT_TIME TARGET_NAME           RA  \
		0    spc0001.a.fits  2003-05-21  05:46:06.300712   1104+1959  11:04:01.12   
		1    spc0002.b.fits  2003-05-21  05:48:25.591305   1104+1959  11:04:01.08   
		2    spc0003.b.fits  2003-05-21  05:50:42.924982   1104+1959  11:04:01.06   
		3    spc0004.a.fits  2003-05-21  05:53:02.194063   1104+1959  11:04:01.08   

	Dependencies
	------------
	astropy.coordinates
	astropy.io
	astropy.time
	astropy.units
	glob
	os.path
	pandas
	'''
# error checking folder 
	if os.path.isdir(folder) == False: 
		raise ValueError('Cannot folder find {} or {}'.format(inp,folder))
	if folder[-1] != '/': folder=folder+'/'

# read in files, with accounting for .gz files
	files = glob.glob(folder+'/*.fits')
	gzflag = 0
	if len(files) == 0: 
		files = glob.glob(folder+'/*.fits.gz')
		gzflag = 1
	if len(files) == 0: 
		raise ValueError('Cannot find any .fits data files in {}'.format(folder))

# generate pandas data frame
	dp = pd.DataFrame()
	dp['FILE'] = [os.path.basename(f) for f in files]
	for k in list(HEADER_DATA.keys()): dp[k] = ['']*len(dp)

# run through each file header and populate the dataframe
	for i,f in enumerate(files):
		hdu = fits.open(f)
		hdu[0].verify('silentfix')
		header = hdu[0].header
		hdu.close()
		for k in list(HEADER_DATA.keys()): 
			ref = ''
			for ii in HEADER_DATA[k]:
				if ii in list(header.keys()) and ref=='': ref=ii
			if ref!='': dp[k].iloc[i] = header[ref]
			if ref=='' and verbose==True and i==0:
				print('Could not find keywords {} in file {}'.format(HEADER_DATA[k],f))
# update some of the mode names
		if 'SHORTXD' in dp['MODE'].iloc[i].upper(): dp['MODE'].iloc[i] = 'SXD'
		if 'lowres' in dp['MODE'].iloc[i].lower(): dp['MODE'].iloc[i] = 'Prism'		
		if 'arc' in dp['FILE'].iloc[i]: 
			dp['TARGET_NAME'].iloc[i] = ARC_NAME
			dp['TARGET_TYPE'].iloc[i] = 'calibration'
		if 'flat' in dp['FILE'].iloc[i]: 
			dp['TARGET_NAME'].iloc[i] = FLAT_NAME
			dp['TARGET_TYPE'].iloc[i] = 'calibration'
# set standards based on integration time
# this is not a great way to do this!
		if dp['TARGET_TYPE'].iloc[i]=='': 
			dp['TARGET_TYPE'].iloc[i]='target'
			if 'SXD' in dp['MODE'].iloc[i] and float(dp['INTEGRATION'].iloc[i])<=30.: dp['TARGET_TYPE'].iloc[i]='standard'
			if 'Prism' in dp['MODE'].iloc[i] and float(dp['INTEGRATION'].iloc[i])<=10.: dp['TARGET_TYPE'].iloc[i]='standard'

# if guess above was wrong, reset all standards back to targets
	dpt = dp[dp['TARGET_TYPE']=='target']
	if len(dpt)==0: 
		dp.loc[dp['TARGET_TYPE']=='standard','TARGET_TYPE'] = 'target'
	dpt = dp[dp['TARGET_TYPE']=='standard']
	if len(dpt)==0: 
		if verbose==True: print('Warning: no standards present in list; be sure to check driver file carefully')

# generate ISO 8601 time string and sort
# --> might need to do some work on this
	dp['DATETIME'] = [dp['UT_DATE'].iloc[i]+'T'+dp['UT_TIME'].iloc[i] for i in range(len(dp))]
	dp['MJD'] = [Time(d,format='isot', scale='utc').mjd for d in dp['DATETIME']]
	dp['INSTRUMENT'] = ['spex']*len(dp)
	if dp['MJD'].iloc[0]>INSTRUMENT_DATE_SHIFT: dp['INSTRUMENT'] = ['uspex']*len(dp)
	dp.sort_values('DATETIME',inplace=True)
	dp.reset_index(inplace=True,drop=True)

# fixed/moving - determined by range of position and motion of soruce
# NOTE: THIS FAILS IF USER DOESN'T CHANGE NAME OF SOURCE
	dp['FIXED-MOVING'] = ['fixed']*len(dp)
	names = list(set(list(dp['TARGET_NAME'])))
	names.remove(ARC_NAME)
	names.remove(FLAT_NAME)
	if len(names)==0:
		if verbose==True: print('Warning: no science files identified in {}'.format(folder))
	else:
		for n in names:
			dps = dp[dp['TARGET_NAME']==n]
			dpsa = dps[dps['BEAM']=='A']
			if len(dpsa)>1:	
				pos1 = SkyCoord(dpsa['RA'].iloc[0]+' '+dpsa['DEC'].iloc[0],unit=(u.hourangle, u.deg))
				pos2 = SkyCoord(dpsa['RA'].iloc[-1]+' '+dpsa['DEC'].iloc[-1],unit=(u.hourangle, u.deg))
				dx = pos1.separation(pos2).to(u.arcsec)
				time1 = Time(dpsa['DATETIME'].iloc[0],format='isot', scale='utc')
				time2 = Time(dpsa['DATETIME'].iloc[-1],format='isot', scale='utc')
				dt = (time2-time1).to(u.hour)
				if dx.value > MOVING_MAXSEP or ((dx/dt).value>MOVING_MAXRATE and dt.value > 0.1):
					dp.loc[dp['TARGET_NAME']==n,'FIXED-MOVING'] = 'moving'
				if verbose==True: print('{}: dx={:.1f} arc, dt={:.1f} hr, pm={:.2f} arc/hr = {}'.format(n,dx,dt,dx/dt,dp.loc[dp['TARGET_NAME']==n,'FIXED-MOVING']))

	return dp


##############################
####### LOG GENERATION #######
##############################

def tablestyle(val):
	'''
	Plotting style for pandas dataframe for html display
	NOT ACTUALLY SURE THIS IS DOING ANYTHING
	'''
	color='white'
	if isinstance(val,str) == True:
		if 'LowRes' in val: color='blue'
		if 'SXD' in val: color='green'
		if 'LXD' in val: color='red'
	return 'background-color: {}'.format(color)


def write_log(dp,log_file='',options={},verbose=ERROR_CHECKING):
	'''
	Purpose
	-------
	Takes in dataFrame from process_folder and generates a log file based on 
	select header values defined in LOG_COLUMNS parameter in LOG_PARAMETERS

	Parameters
	----------
	dp : pandas DataFrame
		output of process_folder

	log_file : str, default=LOG_DEFAULT_FILENAME
		full path to output log file; 
		allowed file formats are .csv, .htm, .html, .json, .psv, .tab, .tex, .tsv, .txt, .xls, .xlsx 

	options : dict, default = {}
		options for log generation parameters that override defaults given in LOG_PARAMETERS,

	verbose : bool, default=ERROR_CHECKING
		Set to True to return verbose output

	Outputs
	-------
	No explicit returns; log file is output as specified by file extension

	Example
	-------
	>>> from pyspextool.batch import batch
	>>> dpath = '/Users/adam/data/spex/20200101/data/'
	>>> dp = batch.process_folder(dpath)
	>>> batch.write_log(dp,dpath+'log.html',verbose=True)
		
		log written to /Users/adam/projects/spex_archive/testing/spex-prism/log.html
		
	Dependencies
	------------
	copy
	os.path
	pandas
	'''
# update default parameters with options
	parameters = copy.deepcopy(LOG_PARAMETERS)
	parameters.update(options)
	if log_file != '': parameters['FILENAME'] = log_file

# downselect columns to save out; by default
	if len(parameters['COLUMNS']) > 0:
		try: dpout = dp[parameters['COLUMNS']]
		except: 
			dpout = copy.deepcopy(dp)
			if verbose==True: print('Warning: could not select subset of columns {} from data frame, saving all columns'.format(parameters['COLUMNS']))
	else: dpout = copy.deepcopy(dp)

# save depends on file name
	ftype = parameters['FILENAME'].split('.')[-1]
	if ftype in ['xls','xlsx']: dpout.to_excel(parameters['FILENAME'],index=False)	
	elif ftype in ['csv']: dpout.to_csv(parameters['FILENAME'],sep=',',index=False)	
	elif ftype in ['tsv','txt','tab']: dpout.to_csv(parameters['FILENAME'],sep='\t',index=False)	
	elif ftype in ['psv']: dpout.to_csv(parameters['FILENAME'],sep='|',index=False)	
	elif ftype in ['tex']: dpout.to_latex(parameters['FILENAME'],longtable=True,index=False)	
	elif ftype in ['json']: dpout.to_json(parameters['FILENAME'])	
	elif ftype in ['htm','html']:
		dpout.style.apply(tablestyle)
		if parameters['HTML_TEMPLATE_FILE'] != '' and os.path.exists(parameters['HTML_TEMPLATE_FILE']):
			with open(parameters['HTML_TEMPLATE_FILE'],'r') as f: dphtml = f.read()
		else: dphtml = copy.deepcopy(parameters['BASE_HTML'])
		for x in ['INSTRUMENT','UT_DATE']:
			if x in list(dp.columns): 
				dphtml = dphtml.replace('[{}]'.format(x),dp[x].iloc[0])
		dphtml = dphtml.replace('[TABLE]',dpout.to_html(classes='table table-striped text-center',index=False,bold_rows=True))
		with open(parameters['FILENAME'],'w') as f: f.write(dphtml)
	else: raise ValueError('Could not write out to {}; unknown file format'.format(parameters['FILENAME']))

	if verbose==True: print('log written to {}'.format(parameters['FILENAME']))
	return


#############################
##### BATCH DRIVER FILE #####
#############################

def read_driver(driver_file,options={},verbose=ERROR_CHECKING):
	'''
	Purpose
	-------
	Reads in batch driver file into a dictionary for processing

	Parameters
	----------
	driver_file : str
		full path to output file for batch driver

	options : dict, default = {}
		options for batch reduction parameters that override defaults given in BATCH_PARAMETERS,
		but are themselves overridden by what is in batch driver file

	verbose : bool, default=ERROR_CHECKING
		set to True to return verbose output

	Outputs
	-------
	Returns dictionary of batch reduction parameters

	Example
	-------
	>>> from pyspextool.batch import batch
	>>> import yaml
	>>> driver_file = '/Users/adam/projects/spex_archive/testing/150512/proc/driver.txt'
	>>> params = batch.read_driver(driver_file)
	>>> print(yaml.dump(params))
		
		ARC_FILE_PREFIX: arc-
		CALIBRATED_FILE_PREFIX: calspec
		CAL_FOLDER: /Users/adam/projects/spex_archive/testing/150512/cals/
		CAL_SETS: 34-39,70-75,92-97,114-119,142-147
		COMBINED_FILE_PREFIX: combspec
		DATA_FOLDER: /Users/adam/projects/spex_archive/testing/150512/data/
		...
		
	Dependencies
	------------
	copy
	os.path
	pandas
	yaml
	'''
# update default parameters with passed options
	parameters = copy.deepcopy(BATCH_PARAMETERS)
	parameters.update(options)

# check presence of file
	if os.path.exists(driver_file)==False:
		raise ValueError('Cannot find batch driver file {}; check path'.format(driver_file))

# read in and clean out comments and newlines
	f = open(driver_file,'r')
	lines = f.readlines()
	f.close()
	lines = list(filter(lambda x: (x != ''), lines))
	lines = list(filter(lambda x: (x != '\n'), lines))
	lines = list(filter(lambda x: (x[0] != '#'), lines))

# extract keyword = value lines outside target observations and use to update parameters
	klines = list(filter(lambda x: ('=' in x), lines))
	klines = list(filter(lambda x: OBSERVATION_SET_KEYWORD not in x, klines))
	klines = [l.replace(' ','').replace('\n','') for l in klines]
	new_kpar = dict([l.strip().split('=') for l in klines])
	parameters.update(new_kpar)

# insert the individual science sets
	slines = list(filter(lambda x: OBSERVATION_SET_KEYWORD in x, lines))
	for i,sline in enumerate(slines):
		spar = copy.deepcopy(OBSERVATION_PARAMETERS_REQUIRED)
		spar.update(OBSERVATION_PARAMETERS_OPTIONAL)
# update with global batch parameters			
		for k in list(spar.keys()):
			if k in list(parameters.keys()):
				if parameters[k]!='': spar[k] = parameters[k]
# update with passed required parameters			
		rpars = sline.replace('\n','').split('\t')
		if len(rpars) < len(list(OBSERVATION_PARAMETERS_REQUIRED.keys())):
			if verbose==True: print('Warning: line\n{}\ncontains fewer than the required parameters\n{}; skipping'.format(sline,OBSERVATION_PARAMETERS_REQUIRED))
		else:
			for ii,k in enumerate(list(OBSERVATION_PARAMETERS_REQUIRED.keys())): spar[k] = rpars[ii+1]
# add optional parameters			
			opars = list(filter(lambda x: ('=' in x), rpars))
			new_opar = dict([l.strip().split('=') for l in opars])
			spar.update(new_opar)
# some instrument specific adjustments
# THIS MIGHT NEED TO BE MANAGED THROUGH OTHER PYSPEXTOOL FUNCTIONS
			if 'Prism' in spar['MODE']: spar['ORDERS'] = '1'
#				if verbose==True: print('Updated prism mode orders to 1')
			if parameters['INSTRUMENT'] == 'spex' and 'SXD' in spar['MODE'] and spar['ORDERS'][-1]=='9':
				spar['ORDERS'] = spar['ORDERS'][:-1]+'8'
#				if verbose==True: print('Updated spex SXD mode orders to {}'.format(spar['ORDERS']))

		parameters['{}{}'.format(OBSERVATION_SET_KEYWORD,str(i+1).zfill(4))] = spar

# some specific parameters that need format conversation
	for k in ['QA_FILE','QA_PLOT']:
		if k in list(parameters.keys()): parameters[k] = bool(parameters[k])


# report out parameters
	if verbose==True:
		print('\nDriver parameters:')
		print(yaml.dump(parameters))

	return parameters


def write_driver(dp,driver_file='driver.txt',data_folder='',options={},create_folders=False,comment='',check=ERROR_CHECKING,verbose=ERROR_CHECKING):
	'''
	Purpose
	-------
	Writes the batch driver file based on pandas DataFrame input from process_folder and 
	additional user-supplied options

	Parameters
	----------
	dp : pandas DataFrame
		output of process_folder

	driver_file : str, default = 'driver.txt'
		full path to output file for batch driver

	data_folder : str, default = ''
		full path to data folder containing raw science files

	options : dict, default = {}
		options for driver parameter that override defaults given in BATCH_PARAMETERS

	create_folders: bool, default = False
		if set to True, create proc, cals, and qa folders if they don't exist

	comment: str, default = ''
		comment string to add to head of driver file for information purposes

	check : bool, default=ERROR_CHECKING
		set to True to check contents of driver file by reading it in and returnin parameters

	verbose : bool, default=ERROR_CHECKING
		set to True to return verbose output

	Outputs
	-------
	If check==True, returns dictionary of batch reduction parameters (output of read_driver)
	otherwise returns nothing; in either case, writes contents to file specified by driver_file


	Example
	-------
	(1) Create from process_folder output:

	>>> from pyspextool.batch import batch
	>>> dpath = '/Users/adam/projects/spex_archive/testing/spex-prism/data/'
	>>> driver_file = '/Users/adam/projects/spex_archive/testing/spex-prism/proc/driver.txt'
	>>> dp = batch.process_folder(dpath,verbose=False)
	>>> pars = batch.write_driver(dp,driver_file=driver_file,data_folder=dpath,create_folders=True,check=True,verbose=True)
			
			Created CALS_FOLDER folder /Users/adam/projects/spex_archive/testing/spex-prism/cals/
			Created PROC_FOLDER folder /Users/adam/projects/spex_archive/testing/spex-prism/proc/
			Created QA_FOLDER folder /Users/adam/projects/spex_archive/testing/spex-prism/qa/

			Batch instructions written to /Users/adam/projects/spex_archive/testing/spex-prism/proc/driver.txt, please this check over before proceeding

			Driver parameters:
			ARC_FILE_PREFIX: arc
			CALIBRATED_FILE_PREFIX: calspec
			CALS_FOLDER: /Users/adam/projects/spex_archive/testing/spex-prism/cals
			CAL_SETS: 15-20
			...

	(2) Create from previously created log file:
	>>> from pyspextool.batch import batch
	>>> import pandas
	>>> dpath = '/Users/adam/projects/spex_archive/testing/spex-prism/data/'
	>>> log_file = '/Users/adam/projects/spex_archive/testing/spex-prism/proc/logs.csv'
	>>> driver_file = '/Users/adam/projects/spex_archive/testing/spex-prism/proc/driver.txt'
	>>> dp = pandas.read_csv(log_file,sep=',')
	>>> pars = batch.write_driver(dp,driver_file=driver_file,data_folder=dpath,create_folders=True, check=True, verbose=True)

			Batch instructions written to /Users/adam/projects/spex_archive/testing/spex-prism/proc/driver.txt, please this check over before proceeding

			Driver parameters:
			ARC_FILE_PREFIX: arc
			CALIBRATED_FILE_PREFIX: calspec
			CALS_FOLDER: /Users/adam/projects/spex_archive/testing/spex-prism/cals
			CAL_SETS: 15-20
			...
		
	Dependencies
	------------
	copy
	numpy
	os.path
	pandas
	read_driver()
	'''
# manage default options
	driver_param = copy.deepcopy(BATCH_PARAMETERS)
	driver_param.update(options)
	if data_folder != '': driver_param['DATA_FOLDER'] = data_folder

# check folders
	if os.path.exists(driver_param['DATA_FOLDER'])==False:
		raise ValueError('Cannot find data folder {}: you must include this in batch driver file'.format(driver_param['DATA_FOLDER']))
	for x in ['CALS_FOLDER','PROC_FOLDER','QA_FOLDER']:
# if blank, create cals, proc, and qa folders parallel to data folder
		if driver_param[x]=='': 
			driver_param[x] = driver_param['DATA_FOLDER'].replace(os.path.split(os.path.dirname(driver_param['DATA_FOLDER']))[-1],(x.split('_')[0]).lower())
		if os.path.exists(driver_param[x])==False:
			if create_folders==True: 
				os.mkdir(driver_param[x])
				if verbose==True: print('Created {} folder {}'.format(x,driver_param[x]))
			else: raise ValueError('Folder {} does not exist; either create or set keyword create_folders to True'.format(driver_param[x]))

# get information from first line of log
	dpc = copy.deepcopy(dp)
	for x in ['INSTRUMENT','UT_DATE','OBSERVER','PROGRAM']:
		driver_param[x] = dpc[x].iloc[0]

# some file name work
# THIS NEEDS TO BE UPDATED WITH INSTRUMENT SPECIFIC INFORMATION
	dpc['PREFIX'] = [x.replace('.a.fits','').replace('.b.fits','') for x in dpc['FILE']]
	n=4
	if driver_param['INSTRUMENT']=='uspex': n=5
	dpc['FILE NUMBER'] = [int(x[-n:]) for x in dpc['PREFIX']]
	dpc['PREFIX'] = [x[:-n] for x in dpc['PREFIX']]

# set up default prefixes for flats and arcs
	dps = dpc[dpc['TARGET_NAME']==FLAT_NAME]
	driver_param['FLAT_FILE_PREFIX'] = dps['PREFIX'].iloc[0]
	dps = dpc[dpc['TARGET_NAME']==ARC_NAME]
	driver_param['ARC_FILE_PREFIX'] = dps['PREFIX'].iloc[0]
	dps = dpc[dpc['TARGET_NAME']!=FLAT_NAME]
	dps = dps[dps['TARGET_NAME']!=ARC_NAME]
	driver_param['SCIENCE_FILE_PREFIX'] = dps['PREFIX'].iloc[0]

# write out instructions
	try: f = open(driver_file,'w')
	except: raise ValueError('Cannot write to {}, check file path and permissions'.format(driver_file))
	f.write('# Batch reduction driver file for {} observations on {}\n'.format(dpc['INSTRUMENT'].iloc[0],dpc['UT_DATE'].iloc[0]))
	if comment!='': f.write('# {}\n'.format(comment.replace('\n','\n# ')))

# observational information
	f.write('\n# Observational information\n')
	for x in ['INSTRUMENT','UT_DATE','PROGRAM','OBSERVER']:
		f.write('{} = {}\n'.format(x,str(driver_param[x])))

# files & folders
	f.write('\n# Folders and files\n')
	for x in ['DATA_FOLDER','CALS_FOLDER','PROC_FOLDER','QA_FOLDER']:
		f.write('{} = {}\n'.format(x,os.path.abspath(str(driver_param[x]))))
	for x in ['SCIENCE_FILE_PREFIX','SPECTRA_FILE_PREFIX','COMBINED_FILE_PREFIX','CALIBRATED_FILE_PREFIX','STITCHED_FILE_PREFIX']:
		f.write('{} = {}\n'.format(x,str(driver_param[x])))

# cals
	f.write('\n# Lamp calibrations\n')
	for x in ['FLAT_FILE_PREFIX','ARC_FILE_PREFIX']:
		f.write('{} = {}\n'.format(x,driver_param[x]))
	cal_sets=''
	dpcal = dpc[dpc['TARGET_TYPE']=='calibration']
	fnum = np.array(dpcal['FILE NUMBER'])
	calf1 = fnum[np.where(np.abs(fnum-np.roll(fnum,1))>1)]
	calf2 = fnum[np.where(np.abs(fnum-np.roll(fnum,-1))>1)]
	for i in range(len(calf1)): cal_sets+='{:.0f}-{:.0f},'.format(calf1[i],calf2[i])
	f.write('CAL_SETS = {}\n'.format(cal_sets[:-1]))

# observations
	dps = dpc[dpc['TARGET_TYPE']=='target']
	names = list(set(list(dps['TARGET_NAME'])))
	names = [str(x) for x in names]
	names.sort()

# no names - close it up
	if len(names)==0:
		if verbose==True: print('Warning: no science files identified; you may need to update the observational logs')
		f.close()
		return

# loop over names
	f.write('\n# Science observations')
	f.write('\n#\tMode\tTarget Type\tTarget Name\tPrefix\tFiles\tFlat Filename\tWavecal Filename\tStd Name\tStd Prefix\tStd Files\tOptions (key=value)\n'.zfill(len(OBSERVATION_SET_KEYWORD)))
	for n in names:
		dpsrc = dpc[dpc['TARGET_NAME']==n]
		line='{}\t{}\t{} ps\t{}\t{}'.format(OBSERVATION_SET_KEYWORD,str(dpsrc['MODE'].iloc[0]),str(dpsrc['FIXED-MOVING'].iloc[0]),n,str(dpsrc['PREFIX'].iloc[0]))
		fnum = np.array(dpsrc['FILE NUMBER'])
		line+='\t{:.0f}-{:.0f}'.format(np.nanmin(fnum),np.nanmax(fnum))

# assign calibrations
		dpcals = dpcal[dpcal['MODE']==dpsrc['MODE'].iloc[0]]
		if len(dpcals)==0: 
			if verbose==True: print('Warning: no calibration files associated with mode {} for source {}'.format(dps['MODE'].iloc[0],n))
			i=0
		else:
			i = np.argmin(np.abs(calf1-np.median(dpsrc['FILE NUMBER'])))
		line+='\tflat{}.fits\twavecal{}.fits'.format(cal_sets.split(',')[i],cal_sets.split(',')[i])

# assign flux cals
		dpflux = dpc[dpc['TARGET_TYPE']=='standard']
		dpflux = dpflux[dpflux['MODE']==dpsrc['MODE'].iloc[0]]
		ftxt = '\t{}\tUNKNOWN\t0-0'.format(str(driver_param['SCIENCE_FILE_PREFIX']))
		if len(dpflux)==0: 
			if verbose==True: print('Warning: no calibration files associated with mode {} for source {}'.format(dps['MODE'].iloc[0],n))
			line+=ftxt
		else:
			dpflux['DIFF1'] = [np.abs(x-np.nanmedian(dpsrc['AIRMASS'])) for x in dpflux['AIRMASS']]
			dpflux['DIFF2'] = [np.abs(x-np.nanmedian(dpsrc['MJD'])) for x in dpflux['MJD']]
			dpflux['DIFF'] = dpflux['DIFF1']+dpflux['DIFF2']
			fref = dpflux['FILE NUMBER'].iloc[np.argmin(dpflux['DIFF'])]
			tname = str(dpflux['TARGET_NAME'].iloc[np.argmin(dpflux['DIFF'])])
			dpfluxs = dpflux[dpflux['TARGET_NAME']==tname]
			fnum = np.array(dpfluxs['FILE NUMBER'])
			if len(fnum)<2: 
				if verbose==True: print('Fewer than 2 flux calibrator files for source {}'.format(tname))
				line+=ftxt
			else:				
				if len(fnum)==2: 
					line+='\t{}\t{}\t{:.0f}-{:.0f}'.format(tname,str(dpfluxs['PREFIX'].iloc[0]),fnum[0],fnum[1])
				else:
					telf1 = fnum[np.where(np.abs(fnum-np.roll(fnum,1))>1)]
					telf2 = fnum[np.where(np.abs(fnum-np.roll(fnum,-1))>1)]
					if len(telf1)==0: line+=ftxt
					elif len(telf1)==1: line+='\t{}\t{}\t{:.0f}-{:.0f}'.format(tname,str(dpfluxs['PREFIX'].iloc[0]),telf1[0],telf2[0])
					else:
						i = np.argmin(np.abs(fref-telf1))
						line+='\t{}\t{}\t{:.0f}-{:.0f}'.format(tname,str(dpfluxs['PREFIX'].iloc[i]),telf1[i],telf2[i])

# all other options
		# if len(extraction_options)>0:
		# 	for k in list(extraction_options.keys()): line+='\t{}={}'.format(k,extraction_options[k])
		f.write(line+'\n')
	f.close()

# report out location
	if verbose==True: 
		print('\nBatch instructions written to {}, please this check over before proceeding'.format(driver_file))

# if check=True, read back in and return parameters
	if check==True:
		return read_driver(driver_file,verbose=verbose)

	return


#############################
##### QUALITY ASSURANCE #####
#############################


def makeQApage(driver_input,log_input,output_folder='',log_html_name='log.html',options={},show_options=False,verbose=ERROR_CHECKING):
	'''
	Purpose
	-------
	Generates the quality assurance page to evaluate a night of reduced data

	Parameters
	----------
	driver_input : str or dict
		either the full path to the batch driver file or the dict output of read_driver()

	log_input : str or pandas DataFrame
		either the full path to the log csv file or the pandas DataFrame output of process_folder()

	output_folder : str, default=''
		by default the page is saved in QA folder; this option specifies the full path 
		to an alternate output folder (e.g. public www folder); where the entire contents 
		of the QA folder is copied; use carefully!

	log_html_name : str, default=''
		full path to the log html file

	options : dict, default = {}
		options for driver parameter that override defaults given in QA_PARAMETERS

	verbose : bool, default=ERROR_CHECKING
		set to True to return verbose output

	Outputs
	-------
	No explicit output, generates an html page for QA review


	Example
	-------
	TBD
		
	Dependencies
	------------
	copy
	numpy
	os.path
	pandas
	read_driver()
	'''
# update default parameters with options
	qa_parameters = copy.deepcopy(QA_PARAMETERS)
	qa_parameters.update(options)

# if necessary read in driver
	if isinstance(driver_input,str)==True:
		if os.path.exists(driver_input)==False: raise ValueError('Cannot find driver file {}'.format(driver_input))
		try: driver = read_driver(driver_input)
		except: raise ValueError('Unable to read in driver file {}'.format(driver_input))
	elif isinstance(driver_input,dict)==True:
		driver = copy.deepcopy(driver_input)
	else: raise ValueError('Do not recognize format of driver input {}'.format(driver_input))
	for x in ['DATA_FOLDER','CALS_FOLDER','PROC_FOLDER','QA_FOLDER']: qa_parameters[x] = driver[x]

# if necessary read in log
	if isinstance(log_input,str)==True:
		if os.path.exists(log_input)==False: raise ValueError('Cannot find log file {}'.format(log_input))
		if log_input.split('.')[-1] != 'csv': raise ValueError('Log input must be a csv file, you passed {}'.format(log_input))
		obslog = pd.read_csv(log_input)
	elif isinstance(log_input,pd.DataFrame)==True: 
		obslog = copy.deepcopy(log_input)
	else: raise ValueError('Do not recognize format of observing log input {}'.format(log_input))

# show the QA parameters if desired
	if show_options==True: print('\nQA Parameters:\n{}\n'.format(yaml.dump(qa_parameters)))

# set up pyspextool parameters
	ps.pyspextool_setup(driver['INSTRUMENT'])

# read in templates and split index
	for x in ['TEMPLATE_FILE','SOURCE_TEMPLATE_FILE']:
		if os.path.exists(qa_parameters[x])==False: raise ValueError('Cannot find QA html template file {}'.format(qa_parameters[x]))
	with open(qa_parameters['TEMPLATE_FILE'],'r') as f: index_txt = f.read()
	output_text,index_bottom = index_txt.split('[SOURCES]')
	with open(qa_parameters['SOURCE_TEMPLATE_FILE'],'r') as f: source_txt = f.read()
	with open(qa_parameters['SINGLE_PLOT_TEMPLATE_FILE'],'r') as f: single_txt = f.read()

# replace relevant parameters in top of index
	output_text = output_text.replace('[CSS_FILE]',qa_parameters['CSS_FILE'])
	output_text = output_text.replace('[LOG_HTML]',log_html_name)
# NEED TO UPDATE WEATHER LINK
	output_text = output_text.replace('[WEATHER_HTML]',qa_parameters['MKWC_ARCHIVE_URL'])

# replace parameters from driver
#	page_parameters = {}
	for x in list(driver.keys()):
		output_text = output_text.replace('[{}]'.format(x),str(driver[x]))

# now cycle through each of the science sets
	scikeys = list(filter(lambda x: OBSERVATION_SET_KEYWORD in x,list(driver.keys())))
	scikeys.sort()
	imodes = []
	for sci in scikeys:
		stxt = copy.deepcopy(source_txt)
		sci_param = driver[sci]
		imodes.append(sci_param['MODE'])
# data from the driver file		
		for x in list(OBSERVATION_PARAMETERS_REQUIRED.keys()):
			stxt = stxt.replace('['+x+']',str(sci_param[x]))
# data from the observing log
		sci_log = obslog[obslog['TARGET_NAME']==sci_param['TARGET_NAME']]
		if len(sci_log)>0:
			for x in ['RA','DEC','AIRMASS','SLIT']:
				stxt = stxt.replace('['+x+']',str(sci_log[x].iloc[0]))
# nicer coordinates
			s = SkyCoord(sci_log['RA'].iloc[0]+' '+sci_log['DEC'].iloc[0], unit=(u.hourangle, u.deg))
			coord = s.to_string('hmsdms',sep=':')
			desig = 'J'+s.to_string('hmsdms',sep='',pad=True).replace('.','').replace(' ','')
			stxt = stxt.replace('[COORDINATE]',coord)
			stxt = stxt.replace('[DESIGNATION]',desig)
			stxt = stxt.replace('[DESIGNATION_URL]',desig.replace('+','%2B'))
			stxt = stxt.replace('[UT_START]',str(sci_log['UT_TIME'].iloc[0]).split('.')[0])
			stxt = stxt.replace('[UT_END]',str(sci_log['UT_TIME'].iloc[-1]).split('.')[0])
#			sci_log['TINT'] = [sci_log.loc['INTEGRATION',i]*sci_log.loc['COADDS',i] for i in range(len(sci_log))]
# NOTE: THIS CURRENTLY DOESN'T TAKE INTO ACCOUNT COADDS AS THE COMMAND ABOVE HAD AN ERROR
			stxt = stxt.replace('[INTEGRATION]','{:.1f}'.format(np.nansum(sci_log['INTEGRATION'])))

# final calibrated file
		ptxt = copy.deepcopy(qa_parameters['HTML_TABLE_HEAD'])
		imfile = glob.glob(os.path.join(qa_parameters['QA_FOLDER'],driver['CALIBRATED_FILE_PREFIX'])+'{}*{}'.format(sci_param['TARGET_FILES'],qa_parameters['PLOT_TYPE']))
		if len(imfile)>0: 
			ptxt+=copy.deepcopy(single_txt).replace('[IMAGE]',os.path.basename(imfile[0])).replace('[IMAGE_WIDTH]',str(qa_parameters['IMAGE_WIDTH']))
		ptxt+=copy.deepcopy(qa_parameters['HTML_TABLE_TAIL'])
		stxt = stxt.replace('[CALIBRATED_FILE]',ptxt)

# insert other target and calibrator files
		for x in ['TARGET','STD']:	

# combined files
			ptxt = copy.deepcopy(qa_parameters['HTML_TABLE_HEAD'])
			imfile = glob.glob(os.path.join(qa_parameters['QA_FOLDER'],'{}{}*_raw{}'.format(driver['COMBINED_FILE_PREFIX'],sci_param['{}_FILES'.format(x)],qa_parameters['PLOT_TYPE'])))
			if len(imfile)>0:
				ptxt+=copy.deepcopy(single_txt).replace('[IMAGE]',os.path.basename(imfile[0])).replace('[IMAGE_WIDTH]',str(qa_parameters['IMAGE_WIDTH']))
			imfile = glob.glob(os.path.join(qa_parameters['QA_FOLDER'],'{}{}*_scaled{}'.format(driver['COMBINED_FILE_PREFIX'],sci_param['{}_FILES'.format(x)],qa_parameters['PLOT_TYPE'])))
			if len(imfile)>0: 
				ptxt+=copy.deepcopy(single_txt).replace('[IMAGE]',os.path.basename(imfile[0])).replace('[IMAGE_WIDTH]',str(qa_parameters['IMAGE_WIDTH']))
			ptxt+=copy.deepcopy(qa_parameters['HTML_TABLE_TAIL'])
			stxt = stxt.replace('[{}_COMBINED_FILES]'.format(x),ptxt)

# individual files
			indexinfo = {'nint': setup.state['nint'], 'prefix': driver['SPECTRA_FILE_PREFIX'],'suffix': '', 'extension': '.pdf'}
			files = make_full_path(qa_parameters['QA_FOLDER'],sci_param['{}_FILES'.format(x)], indexinfo=indexinfo)
			files = list(filter(lambda x: os.path.exists(x),files))
			files.sort()
			ptxt = copy.deepcopy(qa_parameters['HTML_TABLE_HEAD'])
			for i,f in enumerate(files):
				if i>0 and np.mod(i,qa_parameters['NIMAGES'])==0: ptxt+=' </tr>\n <tr> \n'
				ptxt+=copy.deepcopy(single_txt).replace('[IMAGE]',os.path.basename(f)).replace('[IMAGE_WIDTH]',str(qa_parameters['IMAGE_WIDTH']))
			ptxt+=copy.deepcopy(qa_parameters['HTML_TABLE_TAIL'])
			stxt = stxt.replace('[{}_SPECTRA_FILES]'.format(x),ptxt)

# traces and apertures
			fnums = extract_filestring(sci_param['{}_FILES'.format(x)],'index')
			ptxt = copy.deepcopy(qa_parameters['HTML_TABLE_HEAD'])
			cnt = 0
			for f in fnums:
				if cnt>0 and np.mod(cnt,qa_parameters['NIMAGES'])==0: ptxt+=' </tr>\n <tr> \n'
				imfile = glob.glob(os.path.join(qa_parameters['QA_FOLDER'],'{}{}.a_*_trace{}'.format(driver['SCIENCE_FILE_PREFIX'],str(f).zfill(setup.state['nint']),qa_parameters['PLOT_TYPE'])))
				if len(imfile)>0: 
					imfile.sort()
					ptxt+=copy.deepcopy(single_txt).replace('[IMAGE]',os.path.basename(imfile[0])).replace('[IMAGE_WIDTH]',str(qa_parameters['IMAGE_WIDTH']))
					cnt+=1
				if cnt>0 and np.mod(cnt,qa_parameters['NIMAGES'])==0: ptxt+=' </tr>\n <tr> \n'
				imfile = glob.glob(os.path.join(qa_parameters['QA_FOLDER'],'{}{}.a_*_apertureparms{}'.format(driver['SCIENCE_FILE_PREFIX'],str(f).zfill(setup.state['nint']),qa_parameters['PLOT_TYPE'])))
				if len(imfile)>0: 
					imfile.sort()
					ptxt+=copy.deepcopy(single_txt).replace('[IMAGE]',os.path.basename(imfile[0])).replace('[IMAGE_WIDTH]',str(qa_parameters['IMAGE_WIDTH']))
					cnt+=1
			ptxt+=copy.deepcopy(qa_parameters['HTML_TABLE_TAIL'])
			stxt = stxt.replace('[{}_TRACE_APERTURE_FILES]'.format(x.split('_')[0]),ptxt)

# add it into the page
		output_text+=stxt

# list of modes
	imodes = list(set(imodes))
	s=''
	if len(imodes)>0:
		imodes.sort()
		s = str(imodes[0])
		if len(imodes)>1: 
			for x in imodes[1:]: s+=', {}'.format(str(x))
	output_text = output_text.replace('[INSTRUMENT_MODES]',s)

# insert calibrations
	cal_sets = str(driver['CAL_SETS']).split(',')

# flats
	loopstr = '<table>\n <tr>\n'
	cnt=0
	for cs in cal_sets:
		if cnt>0 and np.mod(cnt,qa_parameters['NIMAGES'])==0: loopstr+=' </tr>\n <tr> \n'
		imfile = glob.glob(os.path.join(qa_parameters['QA_FOLDER'],'flat{}_locateorders{}'.format(cs,str(qa_parameters['PLOT_TYPE']))))
		if len(imfile)>0: 
			imfile.sort()
			if qa_parameters['PLOT_TYPE']=='.pdf': loopstr+='  <td align="center">\n   <embed src="{}" width={} height={}>\n   <br>{}\n   </td>\n'.format(os.path.basename(imfile[0]),str(qa_parameters['IMAGE_WIDTH']),str(qa_parameters['IMAGE_WIDTH']),os.path.basename(imfile[0]))
			else: loopstr+='  <td align="center">\n   <img src="{}" width={}>\n   <br>{}\n   </td>\n'.format(os.path.basename(imfile[0]),qa_parameters['IMAGE_WIDTH'],os.path.basename(imfile[0]))
			cnt+=1
	loopstr+=' </tr>\n</table>\n\n'
	index_bottom = index_bottom.replace('[FLATS]',loopstr)

# wavecals
# note: only PDF			
	loopstr = '<table>\n <tr>\n'
	cnt=0
	for cs in cal_sets:
		if cnt>0 and np.mod(cnt,qa_parameters['NIMAGES'])==0: loopstr+=' </tr>\n <tr> \n'
		imfile = glob.glob(os.path.join(qa_parameters['QA_FOLDER'],'wavecal{}_shift.pdf'.format(cs)))
		if len(imfile)>0: 
			imfile.sort()
			loopstr+='  <td align="center">\n   <embed src="{}" width={} height={}>\n   <br>{}\n   </td>\n'.format(os.path.basename(imfile[0]),str(qa_parameters['IMAGE_WIDTH']),str(qa_parameters['IMAGE_WIDTH']),os.path.basename(imfile[0]))
			cnt+=1
	loopstr+=' </tr>\n</table>\n'
	index_bottom = index_bottom.replace('[WAVECALS]',loopstr)

# WRITE OUT HTML FILE
	output_text+=index_bottom
	outfile = os.path.join(qa_parameters['QA_FOLDER'],qa_parameters['FILENAME'])
	try: 
		with open(outfile,'w') as f: f.write(output_text)
	except: raise ValueError('Unable to write QA page to {}; check path and permissions'.format(outfile))

# copy everything to a separate output folder if specified
# RIGHT NOW JUST COPIES ENTIRE FOLDER; COULD BE DONE MORE SURGICALLY
	if output_folder=='': output_folder = qa_parameters['QA_FOLDER']
	if output_folder!=qa_parameters['QA_FOLDER']:
		if os.path.exists(output_folder)==True: 
			try: shutil.copytree(qa_parameters['QA_FOLDER'],output_folder)
			except: 
				print('WARNING: could not copy contents of {} into {}; check path or permissions'.format(qa_parameters['QA_FOLDER'],output_folder))
				output_folder = qa_parameters['QA_FOLDER']
		else: output_folder = qa_parameters['QA_FOLDER']

	if verbose==True: 
		print('\nAcesss QA page at {}\n'.format(os.path.join(qa_parameters['QA_FOLDER'],qa_parameters['FILENAME'])))

	return


#############################
###### BATCH REDUCTION ######
#############################


def batch_reduce(parameters,verbose=ERROR_CHECKING):
	'''
	THIS FUNCTION AND DOCSTRING NEED TO BE UPDATED
	Purpose
	-------
	Primary script for conducting a batch reduction of a data folder

	Parameters
	----------
	parameters : dict or str
		either the dict output of read_driver() or the full path to the batch driver file

	verbose : bool, default=ERROR_CHECKING
		set to True to return verbose output

	Outputs
	-------
	If check==True, returns dictionary of batch reduction parameters (output of read_driver)
	otherwise returns nothing; in either case, writes contents to file specified by driver_file


	Example
	-------
	(1) Create from process_folder output:

	>>> from pyspextool.batch import batch
	>>> dpath = '/Users/adam/projects/spex_archive/testing/spex-prism/data/'
	>>> driver_file = '/Users/adam/projects/spex_archive/testing/spex-prism/proc/driver.txt'
	>>> dp = batch.process_folder(dpath,verbose=False)
	>>> pars = batch.write_driver(dp,driver_file=driver_file,data_folder=dpath,create_folders=True,check=True,verbose=True)
			
			Created CALS_FOLDER folder /Users/adam/projects/spex_archive/testing/spex-prism/cals/
			Created PROC_FOLDER folder /Users/adam/projects/spex_archive/testing/spex-prism/proc/
			Created QA_FOLDER folder /Users/adam/projects/spex_archive/testing/spex-prism/qa/

			Batch instructions written to /Users/adam/projects/spex_archive/testing/spex-prism/proc/driver.txt, please this check over before proceeding

			Driver parameters:
			ARC_FILE_PREFIX: arc
			CALIBRATED_FILE_PREFIX: calspec
			CALS_FOLDER: /Users/adam/projects/spex_archive/testing/spex-prism/cals
			CAL_SETS: 15-20
			...

	(2) Create from previously created log file:
	>>> from pyspextool.batch import batch
	>>> import pandas
	>>> dpath = '/Users/adam/projects/spex_archive/testing/spex-prism/data/'
	>>> log_file = '/Users/adam/projects/spex_archive/testing/spex-prism/proc/logs.csv'
	>>> driver_file = '/Users/adam/projects/spex_archive/testing/spex-prism/proc/driver.txt'
	>>> dp = pandas.read_csv(log_file,sep=',')
	>>> pars = batch.write_driver(dp,driver_file=driver_file,data_folder=dpath,create_folders=True, check=True, verbose=True)

			Batch instructions written to /Users/adam/projects/spex_archive/testing/spex-prism/proc/driver.txt, please this check over before proceeding

			Driver parameters:
			ARC_FILE_PREFIX: arc
			CALIBRATED_FILE_PREFIX: calspec
			CALS_FOLDER: /Users/adam/projects/spex_archive/testing/spex-prism/cals
			CAL_SETS: 15-20
			...
		
	Dependencies
	------------
	copy
	numpy
	os.path
	pandas
	read_driver()
	'''
# check parameters
	if 'VERBOSE' not in list(parameters.keys()): parameters['VERBOSE']=verbose
	parameters['VERBOSE'] = (parameters['VERBOSE'] or verbose)
# set up instrument
	ps.pyspextool_setup(parameters['INSTRUMENT'],raw_path=parameters['DATA_FOLDER'], cal_path=parameters['CALS_FOLDER'], \
		proc_path=parameters['PROC_FOLDER'], qa_path=parameters['QA_FOLDER'],qa_extension=parameters['PLOT_TYPE'],verbose=parameters['VERBOSE'])

# reduce all calibrations
	cal_sets = parameters['CAL_SETS'].split(',')
	for cs in cal_sets:

# flats
		indexinfo = {'nint': setup.state['nint'], 'prefix': parameters['FLAT_FILE_PREFIX'],\
			'suffix': '.a', 'extension': '.fits'}
		temp_files = make_full_path(parameters['DATA_FOLDER'],cs, indexinfo=indexinfo)
		input_files = list(filter(lambda x: os.path.exists(x),temp_files))
		if len(input_files)==0: raise ValueError('Cannot find any of the flat files {}'.format(temp_files))
		fnum = [int(os.path.basename(f).replace(parameters['FLAT_FILE_PREFIX'],'').replace('.a.fits','').replace('.b.fits','')) for f in input_files]
		fstr = '{:.0f}-{:.0f}'.format(np.nanmin(fnum),np.nanmax(fnum))
# NOTE: qa_file force to False due to ploting error in plot_image
		ps.extract.make_flat([parameters['FLAT_FILE_PREFIX'],fstr],'flat{}'.format(cs),qa_plot=parameters['QA_PLOT'],qa_file=parameters['QA_FILE'],verbose=parameters['VERBOSE'])

# wavecal
		indexinfo = {'nint': setup.state['nint'], 'prefix': parameters['ARC_FILE_PREFIX'],\
			'suffix': '.a', 'extension': '.fits'}
		temp_files = make_full_path(parameters['DATA_FOLDER'],cs, indexinfo=indexinfo)
		input_files = list(filter(lambda x: os.path.exists(x),temp_files))
		if len(input_files)==0: raise ValueError('Cannot find any of the arc files {}'.format(temp_files))
		fnum = [int(os.path.basename(f).replace(parameters['ARC_FILE_PREFIX'],'').replace('.a.fits','').replace('.b.fits','')) for f in input_files]
		fstr = '{:.0f}-{:.0f}'.format(np.nanmin(fnum),np.nanmax(fnum))
# HARD CODED choice of use_stored_solution for spex prism mode
# needs to be done on a case-by-case basis due to potentially mixed calibrations
# would be better to move this directly into ps.extrac.make_wavecal function
		hdu = fits.open(input_files[0])
		hdu[0].verify('silentfix')
		header = hdu[0].header
		hdu.close()
#		use_stored_solution = False
#		if 'lowres' in header['GRAT'].lower(): use_stored_solution = True
		ps.extract.make_wavecal([parameters['ARC_FILE_PREFIX'],fstr],'flat{}.fits'.format(cs),'wavecal{}'.format(cs),\
			qa_plot=parameters['QA_PLOT'],qa_file=parameters['QA_FILE'],verbose=parameters['VERBOSE'])

# extract all sources and standards
	bkeys = list(filter(lambda x: OBSERVATION_SET_KEYWORD not in x,list(parameters.keys())))
	scikeys = list(filter(lambda x: OBSERVATION_SET_KEYWORD in x,list(parameters.keys())))
	if len(scikeys)==0: 
		if parameters['VERBOSE']==True: print('No science files to reduce')
		return
	for k in scikeys:
		spar = parameters[k]
		for kk in bkeys:
			if kk not in list(spar.keys()): spar[kk] = parameters[kk]


# SCIENCE TARGET
# reduce the first pair of targets
# NOTE: WOULD BE HELPFUL TO ADD CHECK THAT FILES HAVEN'T ALREADY BEEN REDUCED
		ps.extract.load_image([spar['TARGET_PREFIX'],extract_filestring(spar['TARGET_FILES'],'index')[:2]],\
			spar['FLAT_FILE'], spar['WAVECAL_FILE'],reduction_mode=spar['REDUCTION_MODE'], \
			flat_field=True, linearity_correction=True,qa_plot=spar['QA_PLOT'],qa_file=spar['QA_FILE'], verbose=spar['VERBOSE'])

# set extraction method
		ps.extract.set_extraction_type(spar['TARGET_TYPE'].split(' ')[-1])

# make spatial profiles
		ps.extract.make_spatial_profiles(qa_plot=spar['QA_PLOT'],qa_file=spar['QA_FILE'], verbose=spar['VERBOSE'])

# identify aperture positions
		ps.extract.locate_aperture_positions(spar['NPOSITIONS'], method=spar['APERTURE_METHOD'], qa_plot=spar['QA_PLOT'],qa_file=spar['QA_FILE'], verbose=spar['VERBOSE'])

# select orders to extract (set above)
		ps.extract.select_orders(include=spar['ORDERS'], qa_plot=spar['QA_PLOT'],qa_file=spar['QA_FILE'], verbose=spar['VERBOSE'])

# trace apertures
		ps.extract.trace_apertures(qa_plot=spar['QA_PLOT'],qa_file=spar['QA_FILE'], verbose=spar['VERBOSE'])

# define the aperture - psf, width, background
# NOTE - ONLY WORKS FOR POINT SOURCE, NEED TO FIX FOR XS?
		ps.extract.define_aperture_parameters(spar['APERTURE'], psf_radius=spar['PSF_RADIUS'],bg_radius=spar['BACKGROUND_RADIUS'],\
						bg_width=spar['BACKGROUND_WIDTH'],qa_plot=spar['QA_PLOT'],qa_file=spar['QA_FILE'],)

# extract away
		ps.extract.extract_apertures(verbose=spar['VERBOSE'])

# conduct extraction of all remaining files
		fnum = extract_filestring(spar['TARGET_FILES'],'index')[2:]
		if len(fnum)>0:
			ps.extract.do_all_steps([spar['SCIENCE_FILE_PREFIX'],'{:.0f}-{:.0f}'.format(np.nanmin(fnum),np.nanmax(fnum))],verbose=spar['VERBOSE'])

# FLUX STANDARD
# now reduce the first pair of standards
# NOTE: WOULD BE HELPFUL TO ADD CHECK THAT FILES HAVEN'T ALREADY BEEN REDUCED
		ps.extract.load_image([spar['SCIENCE_FILE_PREFIX'],extract_filestring(spar['STD_FILES'],'index')[:2]],\
			spar['FLAT_FILE'], spar['WAVECAL_FILE'],reduction_mode=spar['REDUCTION_MODE'], \
			flat_field=True, linearity_correction=True,qa_plot=spar['QA_PLOT'],qa_file=spar['QA_FILE'], verbose=spar['VERBOSE'])

# set extraction method
		ps.extract.set_extraction_type(spar['TARGET_TYPE'].split(' ')[-1])

# make spatial profiles
		ps.extract.make_spatial_profiles(qa_plot=spar['QA_PLOT'],qa_file=spar['QA_FILE'], verbose=spar['VERBOSE'])

# identify aperture positions
		ps.extract.locate_aperture_positions(spar['NPOSITIONS'], method=spar['APERTURE_METHOD'], qa_plot=spar['QA_PLOT'],qa_file=spar['QA_FILE'], verbose=parameters['VERBOSE'])

# select orders to extract (set above)
		ps.extract.select_orders(include=spar['ORDERS'], qa_plot=spar['QA_PLOT'],qa_file=spar['QA_FILE'], verbose=spar['VERBOSE'])

# trace apertures
		ps.extract.trace_apertures(qa_plot=spar['QA_PLOT'],qa_file=spar['QA_FILE'], verbose=spar['VERBOSE'])

# define the aperture - psf, width, background
# NOTE - ONLY WORKS FOR POINT SOURCE, NEED TO FIX FOR XS?
		ps.extract.define_aperture_parameters(spar['APERTURE'], psf_radius=spar['PSF_RADIUS'],bg_radius=spar['BACKGROUND_RADIUS'],\
						bg_width=spar['BACKGROUND_WIDTH'], qa_plot=spar['QA_PLOT'],qa_file=spar['QA_FILE'])

# extract away
		ps.extract.extract_apertures(verbose=verbose)

# conduct extraction of all remaining files
		fnum = extract_filestring(spar['STD_FILES'],'index')[2:]
		if len(fnum)>0:
			ps.extract.do_all_steps([spar['SCIENCE_FILE_PREFIX'],'{:.0f}-{:.0f}'.format(np.nanmin(fnum),np.nanmax(fnum))],verbose=spar['VERBOSE'])


# COMBINE SPECTRAL FILES
# NOTE: SPECTRA_FILE_PREFIX is currently hardcoded in code to be 'spectra' - how to change?
# ALSO: currently not plotting SXD spectra due to an error in combined file code
		if spar['MODE']=='SXD':
			ps.combine.combine_spectra(['spectra',spar['TARGET_FILES']],'{}{}'.format(spar['COMBINED_FILE_PREFIX'],spar['TARGET_FILES']),
				scale_spectra=True,scale_range=spar['SCALE_RANGE'],correct_spectral_shape=False,qa_plot=spar['QA_PLOT'],qa_file=spar['QA_FILE'],verbose=spar['VERBOSE'])
		else:
			ps.combine.combine_spectra(['spectra',spar['TARGET_FILES']],'{}{}'.format(spar['COMBINED_FILE_PREFIX'],spar['TARGET_FILES']),
				scale_spectra=True,scale_range=spar['SCALE_RANGE'],correct_spectral_shape=False,qa_plot=spar['QA_PLOT'],qa_file=spar['QA_FILE'],verbose=spar['VERBOSE'])

# telluric star
		if spar['MODE']=='SXD':
			ps.combine.combine_spectra(['spectra',spar['STD_FILES']],'{}{}'.format(spar['COMBINED_FILE_PREFIX'],spar['STD_FILES']),
				scale_spectra=True,scale_range=spar['SCALE_RANGE'],correct_spectral_shape=True,qa_plot=spar['QA_PLOT'],qa_file=spar['QA_FILE'],verbose=spar['VERBOSE'])
		else:
			ps.combine.combine_spectra(['spectra',spar['STD_FILES']],'{}{}'.format(spar['COMBINED_FILE_PREFIX'],spar['STD_FILES']),
				scale_spectra=True,scale_range=spar['SCALE_RANGE'],correct_spectral_shape=True,qa_plot=spar['QA_PLOT'],qa_file=spar['QA_FILE'],verbose=spar['VERBOSE'])

# FLUX CALIBRATE - NOT CURRENTLY IMPLEMENTED
# depends on fixed or moving source which method we use
		if (spar['TARGET_TYPE'].split(' ')[0]).strip()=='fixed':
			if spar['VERBOSE']==True: print('fixed target; doing standard telluric correction and flux calibration')
		elif (spar['TARGET_TYPE'].split(' ')[0]).strip()=='moving':
			if spar['VERBOSE']==True: print('moving target; doing reflectance telluric correction and flux calibration')
		else:
			if spar['VERBOSE']==True: print('Target type {} not recognized, skipping telluric correction and flux calibration'.format(spar['TARGET_TYPE'].split(' ')[0]))


	return




# external function call
if __name__ == '__main__':
	if len(sys.argv) < 4:
		print('Call this function from the command line as python batch.py [data_folder] [cals_folder] [proc_folder]')
	else:
		dp = process_folder(sys.argv[1])
		dp = process_folder(sys.argv[1])
		if len(sys.argv) > 4: log_file = sys.argv[4]
		else: log_file = sys.argv[3]+'log.html'
		print('\nWriting log to {}'.format(log_file))
		write_log(dp,log_file)
		if len(sys.argv) > 5: driver_file = sys.argv[5]
		else: driver_file = sys.argv[3]+'driver.txt'
		print('\nWriting batch driver file to {}'.format(driver_file))
		write_driver(dp,driver_file,data_folder=sys.argv[1],cals_folder=sys.argv[2],proc_folder=sys.argv[3])
		txt = input('\nCheck the driver file {} and press return when ready to proceed...\n')
		par = read_driver(driver_file)
		print('\nReducing spectra')
		batch_reduce(par)
		print('\nReduction complete: processed files are in {}'.format(sys.argv[3]))

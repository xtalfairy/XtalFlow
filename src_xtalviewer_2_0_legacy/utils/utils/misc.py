# -*- coding: utf-8 -*-
import os
import sys
import csv, json
import getpass
import traceback
import warnings
#from PyQt5.QtWidgets import QApplication, QMessageBox

#General functions
def createFolder(directory) :
    try :
        if not os.path.exists(directory):
            os.makedirs(directory)
    except OSError :
        print('[Error] Creating directory: %s'%directory)


def symlink_sf(src, dst) :
    try :
        if os.path.exists(dst) :
            os.unlink(dst)
            os.symlink(src, dst)
            #print ('link replaced:{0}-{1}'.format(src,dst))
        else :
            os.symlink(src, dst)
            #print ('link created::{0}-{1}'.format(src,dst))
    except :
        print ('[!] linking error:{0}-{1}'.format(src,dst))
    return 0


def readRange(strr):
    components = []
    try:
        convstr1 = strr.replace('  ', ' ')
        convstr2 = convstr1.replace(' ', ',')
        convstr3 = convstr2.replace(',,',',')
        ranges   = convstr3.split(',')
        for item in ranges:
            rg = item.replace(' ', '')
            if '-' in rg:
                start = int( rg.split('-')[0]  )
                end   = int( rg.split('-')[-1] )
                for i in range(start, end+1):
                    components.append(i)
            elif '~' in rg:
                start = int( rg.split('~')[0]  )
                end   = int( rg.split('~')[-1] )
                for i in range(start, end+1):
                    components.append(i)
            else:
                components.append( int(rg) )   
        components_ = set(components)
        components  = sorted(list(components))       
    except ValueError as e:
        traceback.print_exc()
    return components


def latestDir(target_dir, protocol_prefix) :
    compare_list={}
    for subdir in os.listdir(target_dir) :
        if subdir.startswith(protocol_prefix) :
            fullpath = '{0}/{1}'.format(target_dir, subdir)
            compare_list[subdir] = os.path.getctime(fullpath)
            #print ('{} results are found'.format(subdir))
        else :
            pass
    latestDirectory = max(compare_list, key=compare_list.get)
    return latestDirectory

def latestFile(target_dir, prefix, filetype) :
    compare_list={}
    for item in os.listdir(target_dir) :
        print(item)
        if item.startswith(prefix) and item.endswith(filetype) :
            fullpath = '{0}/{1}'.format(target_dir, item)
            compare_list[item] = os.path.getmtime(fullpath)
        else :
            pass
    
    latestfile = max(compare_list, key=compare_list.get)
    return latestfile

def csvToList (filename) :
    datalist = []
    try: 
        with open(filename, 'r') as f :
            csvReader = csv.reader(f)
            for item in csvReader :
                datalist.append(item)
    except PermissionError as e:
        warnings.warn("PermissionError", UserWarning)
        #print("[PermissionError] Cannot create : {0}".format(filename))
    return datalist

def listToCsv (savepath, name, listname):
    if not os.path.isdir(savepath):
        createFolder(savepath)
        #print('dir created')
    else: pass
    filename = '{0}/{1}.csv'.format(savepath, name)
    uniq = 1
    while os.path.isfile(filename):
        filename = '{0}/{1}_{2}.csv'.format(savepath, name, str(uniq).zfill(2))
        uniq += 1 
    try:
        with open(filename, 'w') as f:
            csvWriter = csv.writer(f)
            for line in listname:
                csvWriter.writerow(line)    
    except PermissionError as e:
        warnings.warn("PermissionError\n{0}".format(filename), UserWarning)
    return 0

def jsonWriter(savepath, name, record):
    #if not os.path.isdir(savepath):
    #    createFolder(savepath)
    #else: pass
    filename = '{0}/{1}.json'.format(savepath, name)
    with open(filename, 'w') as json_file:
        json.dump(record, json_file, ensure_ascii=False, indent="\t")
    print('[jsonWriter] ', filename)
    return 0


def getUsername():
    username = getpass.getuser()
    if username == 'root':
        username = 'jjh'
    else: pass
    return username

def configToDict (filepath):
    configDic = {}
    with open(filepath, 'r') as f:
        lines = f.readlines()
    #for i,line in enumerate(lines):
    for line in lines:
        key = line.split(':')[0].strip()
        val = line.split(':')[1].strip()
        configDic[key] = val    
    return configDic

def libraryToDict (filepath):
    #library = [ ['ChemID', 'Vendor', 'Library', 'PlateID', 'PlateWell', 'Formula', 'MW', 'Conc', 'Solvent', 'SMILE'] ]  
    libcsv = []
    library = []
    column = {}
    plates = []
    #JJH 20220412 to prevent some letters read with '\ufeff' 
    #with open(filepath, 'r') as f:
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        csvReader = csv.reader(f)
        for item in csvReader:
            libcsv.append(item)
    for i,item in enumerate(libcsv[0]):
        #print(i, item.lower())
        column[item.lower().strip()] = i

    print(column)
    for line in libcsv[1:]:
        chemdict = {}
        chemdict['ChemID']      = line[ column['id'] ]         
        chemdict['Vendor']      = line[ column['vendor'] ]
        chemdict['Library']     = line[ column['library'] ]
        chemdict['PlateID']     = line[ column['plate_id'] ]
        chemdict['PlateWell']   = line[ column['plate_well'] ]
        chemdict['Formula']     = line[ column['formula'] ]
        chemdict['MW']          = line[ column['mw'] ]
        chemdict['SMILE']       = line[ column['smile'] ]
        chemdict['Conc']        = line[ column['conc_mm'] ]
        chemdict['Solvent']     = line[ column['solvent'] ]
        library.append(chemdict)
        #print('chemdict', chemdict)
        if chemdict['PlateID'] not in plates:
            plates.append(chemdict['PlateID'])
        else: pass
    return library, plates

def spareLibToDict(filepath, donelist):
    #library = [ ['ChemID', 'Vendor', 'Library', 'PlateID', 'PlateWell', 'Formula', 'MW', 'Conc', 'Solvent', 'SMILE'] ]  
    libcsv = []
    library = []
    
    column = {}
    plates = []
    #JJH 20220412 to prevent some letters read with '\ufeff' 
    #with open(filepath, 'r') as f:
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        csvReader = csv.reader(f)
        for item in csvReader:
            libcsv.append(item)
    for i,item in enumerate(libcsv[0]):
        #print(i, item.lower())
        column[item.lower().strip()] = i

    #print('donelist', donelist)
    print('livcsv', len(libcsv))
    done = False
    for line in libcsv: 
        chemdict = {}
        chemdict['ChemID']      = line[ column['id'] ]
        chemdict['Vendor']      = line[ column['vendor'] ]
        chemdict['Library']     = line[ column['library'] ]
        chemdict['PlateID']     = line[ column['plate_id'] ]
        chemdict['PlateWell']   = line[ column['plate_well'] ]
        chemdict['Formula']     = line[ column['formula'] ]
        chemdict['MW']          = line[ column['mw'] ]
        chemdict['SMILE']       = line[ column['smile'] ]
        chemdict['Conc']        = line[ column['conc_mm'] ]
        chemdict['Solvent']     = line[ column['solvent'] ]
        chemKey = '{0} {1}'.format(chemdict['PlateID'], chemdict['PlateWell'])
        if chemKey in donelist:
            pass
        else: 
            library.append(chemdict)
    #print('chemdict', chemdict)
    if chemdict['PlateID'] not in plates:
        plates.append(chemdict['PlateID'])
    else: pass

    print('newlivb')
    print(len(library))
    return library, plates



def calEachSites(vol, site):
    drops = vol / 2.5
    eachSites = []
    try:
        oneSite = round(drops / site) * 2.5
        for i in range(1, site+1):
            if i != site:
                vol -= oneSite
                eachSites.append( oneSite )
            else:
                eachSites.append( vol )

    except ZeroDivisionError:
        oneSite = 0
    return eachSites

def transPlate(plate, well):
    plateGrid = {"row" : ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'], \
                 "col" : [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], \
                 "baby": ['a','x', 'c','d']}
    GridSwiss3 = []
    for i in plateGrid["row"]:
        for j in plateGrid["col"]:
            for k in ['a', 'x']:
                w = '{0}{1}{2}'.format(i,str(j).zfill(2), k)
                GridSwiss3.append(w)
        for j in plateGrid["col"]:
            for k in ['c', 'd']:
                w = '{0}{1}{2}'.format(i,str(j).zfill(2), k)
                GridSwiss3.append(w)
    Grid384 = []
    for i in range(1,25):
        for j in range(65, 81):
            w384 = '{0}{1}'.format(chr(j),str(i).zfill(2))
            Grid384.append(w384)


    if plate == 'SwissCI-MRC-3d':
        no = GridSwiss3.index(well)
    elif plate == 'SwissCI-MRC-2d':
        no = GridSwiss2.index(well)
    else: pass
    trans = Grid384[no]
    return trans


def sample_list (ProteinGroupPath) :
    samplelist = []
    with open('{0}/summary_full.csv'.format(ProteinGroupPath)) as f :
        csvReader = csv.reader(f)
        for item in csvReader :
            """
            if item[0] not in samplelist :
                samplelist.append(item[0])
            else : pass
            """
            samplelist.append(item[0])
    list(set(samplelist[1:]))
    return samplelist

def identifyColumn(datalist):
    columnDic = {}
    for i, val in enumerate(datalist[0]):
        columnDic[val] = i
    return columnDic

def isNumber(string):
    try:
        float(s)
        return True
    except ValueError:
        return False



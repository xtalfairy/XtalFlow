import os
import csv
import getpass
from . import misc, adpt
from . import dialogs
from datetime import datetime

import cv2
import math
import re, json


def screenSetting(xShift, yShift, imgScale, configPath):
    f = open(configPath, 'w')
    f.write("xShift:{0}\n".format(str(xShift)))
    f.write("yShift:{0}\n".format(str(yShift)))
    f.write("imgScale:{0}\n".format(str(imgScale)))
    f.close()
    print("Current X,Y/ImgScale is Fixed in this location")
    return 0


def CatalogueThinning(plateCatalogue):
    #self.plateCatalogue format = {'plateID':[plate existance, profile, profile existance, ranked or not, n(target)], ...}
    #self.plateCatalogue format = {'plateID':{'plate': 'present', 'profile': 'profileID_1', 'image': 'present', 'drops': 288, 'rank': 'none', 'selected': 0, 'warning': 'none'} }
    thinnedDict = {}
    message = []
    #print(plateCatalogue)
    for key,val in plateCatalogue.items():
        print(key,val)
        if val['plate'] == 'missing':
            message.append( "pID{0} not found".format(key) )
        else:
            if val['image'] =='missing':
                message.append( "pID{0} has No {1} Image".format(key, val['profile']) )
            else:
                thinnedDict[key] = val
    return thinnedDict, message



def get_latest_plate_path(pID, rmsPath):
    ('here', pID)
    """pID에 따라 알맞은 경로를 반환하는 함수"""
    if int(pID) > 999 and int(pID) < 1100:
        estimated_path =  "{0}/{1}/plateID_{2}".format(rmsPath, pID[2:], pID)
    elif int(pID) >= 1100 and int(pID) < 2000:
        estimated_path =  "{0}/{1}/plateID_{2}".format(rmsPath, pID[1:], pID)
    elif int(pID) >= 2000 and int(pID) < 2100:
        estimated_path =  "{0}/{1}/plateID_{2}".format(rmsPath, pID[2:], pID)
        print(estimated_path)
    else:
        estimated_path =  "{0}/{1}/plateID_{2}".format(rmsPath, pID, pID)
    latest_batch = misc.latestDir(estimated_path, 'batchID')
    latest_path = "{0}/{1}".format(estimated_path, latest_batch)
    if os.path.exists(estimated_path):
        return latest_batch, latest_path
    else: return False




def find_latest_plate_path(pID, imgProfile, rmsPath):
    ## pID를 정수로 변환 >> 24년 11월부터 pID를 정수로 받기 때문에 이 부분은 사실 필요 없음
    #try:
    #    pID_int = int(pID)
    #except ValueError:
    #    print("[ERROR] pID must be a valid integer.")
    #    return None, None

    # plate_path 설정
    if 999 < pID < 1100:
        plate_path = f"{rmsPath}/{str(pID)[2:]}/plateID_{str(pID)}"
    elif 1100 <= pID < 2000:
        plate_path = f"{rmsPath}/{str(pID)[1:]}/plateID_{str(pID)}"
    else:
        plate_path = f"{rmsPath}/{str(pID)}/plateID_{str(pID)}"

    # 가장 최신 batchID_* 디렉터리를 찾기 위한 변수 초기화
    latest_batch = None
    latest_time = 0
    batch_pattern = re.compile(r'batchID_(\d+)')

    # plate_path에서 직접 하위 디렉터리 목록을 가져옴
    if os.path.exists(plate_path):
        for item in os.listdir(plate_path):
            # item이 batchID_* 패턴인지 확인
            if batch_pattern.match(item):
                batch_path = os.path.join(plate_path, item)
                # profileID_1이 있는지 확인
                if os.path.exists(os.path.join(batch_path, "wellNum_2", imgProfile)):
                    # batchID에서 숫자를 추출하고 최신 시간을 비교
                    batch_id = int(batch_pattern.match(item).group(1))
                    if batch_id > latest_time:
                        latest_time = batch_id
                        latest_batch = item

    # 결과를 생성
    if latest_batch:
        #print(f"[find_latest_plate_path] latest_batch")
        return latest_batch, os.path.join(plate_path, latest_batch)
    else:
        print(f"[findBatch] No valid batch found: {str(pID)}")
        return None, None


 

def surveyPlate(pID, imgProfile, rmsPath, cellarPath):

    plateGrid = {"row" : ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'], \
                 "col" : [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], \
                 "baby": ['a','c','d']}
    #survey = [existance, profile, imgExistance, n(drops), ranked, n(selected)]
    #survey = ['missing', imgProfile, 'missing', '0', 'none', '0']
    survey = { 'plate':'missing', 'profile':imgProfile, 'image':'missing', \
               'drops':0, 'rank':'none', 'selected':0, \
               'warning':'none'}
    imgPaths = {}

    if not os.path.exists(rmsPath) or not os.path.isdir(rmsPath):
        #print(f"Error: Storage path does not exist: {rmsPath}")
        dialogs.show_warning(f"Error: Storage path does not exist: {rmsPath}")
        return survey

    last_batch, plate_path = find_latest_plate_path(pID, imgProfile, rmsPath)
    if not plate_path:
        survey['warning'] = f"No plate path found for pID {pID}"
        return survey
    else:
        survey['plate'] = 'present' 
        number = {'wells':0, 'drops':0}   
        for row in plateGrid['row']:
            for col in plateGrid['col']:
                number['wells'] += 1
                rmsWell_id = f"wellNum_{number['wells']}"
                well_path = "{0}/{1}/{2}".format(plate_path, rmsWell_id, imgProfile)
                if os.path.isdir(well_path):
                    for i, drop in enumerate(plateGrid['baby']):
                        rmsSubwell_id     = f"d{i+1}"
                        uniWell_id = "{0}{1:02d}{2}".format(row, col, drop)
                        img_list = [item for item in sorted(os.listdir(well_path)) if item.startswith(rmsSubwell_id) and item.endswith('ef.jpg')]
                        if len(img_list) > 0:
                            img_name = img_list[-1]
                            img_path = f"{well_path}/{img_name}"
                            if os.path.isfile(img_path):
                                imgPaths[uniWell_id] = img_path
                                number['drops'] += 1
        if number['drops'] > 0:
            survey['image'] = 'present'
            survey['drops'] = number['drops']
        else:
            survey['warning'] = f'pID{pID}: No {imgProfile} image'
        survey['imgPath'] = imgPaths
    file_name = f"{pID}_{last_batch}_{imgProfile}"
    saveSurveyToJson(file_name, survey, cellarPath)

    return survey


def readPlate(pID, plateType, imgProfile, rmsPath, cellarPath):
    print(f'[readPlate] {pID} {plateType} {imgProfile} {rmsPath} {cellarPath}')
    plateGrid = { 'SwissCI-MRC-3d' : { "row" : ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'], \
                                       "col" : [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], \
                                       "baby": ['a','c','d'] }, \
                  'SwissCI-MRC-2d' : { "row" : ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'], \
                                       "col" : [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], \
                                       "baby": ['a','b'] } }
 
    #result = { pID : { 'drops': 0, 'selected': 0, 'info':{} } }
    result = { 'plate_id':pID, 'drops': 0, 'eof': 0, 'selected': 0, 'info':{} }
    if not os.path.exists(rmsPath) or not os.path.isdir(rmsPath):
        print(f"[readPlate] Storage path does not exist: {rmsPath}")
        #dialogs.show_warning(f"Error: Storage path does not exist: {rmsPath}")
        return None
    last_batch, plate_path = find_latest_plate_path(pID, imgProfile, rmsPath)
    if not plate_path:
        #print(f"Eirror: Plate path does not exist: {pID}")
        #dialogs.show_warning(f"Error: Plate path does not exist: {pID}")
        return None
    else:
        well_cnt = 0
        for row in plateGrid[plateType]['row']:
            for col in plateGrid[plateType]['col']:
                well_cnt += 1
                rmsWell_id = f"wellNum_{well_cnt}"
                well_path = f"{plate_path}/{rmsWell_id}/{imgProfile}"
                if os.path.isdir(well_path):
                    for i, drop in enumerate(plateGrid[plateType]['baby']):
                        rmsSubwell_id = f"d{i+1}"
                        uniWell_id = f"{row}{col:02d}{drop}"
                        #result에 키 초기화
                        if uniWell_id not in result['info']:
                            result['info'][uniWell_id] = {
                                'wellNo': rmsWell_id,
                                'Subwell': i + 1
                            }

                        img_list = [item for item in sorted(os.listdir(well_path)) if item.startswith(rmsSubwell_id) and item.endswith('ef.jpg')]
                        if img_list:
                            img_name = img_list[-1]
                            img_path = f"{well_path}/{img_name}"
                            if os.path.isfile(img_path):
                                result['drops'] += 1
                                result['info'][uniWell_id]['imgPath'] = img_path
    #result['eof'] = len(result['info'])
    file_name = f"{str(pID)}_{last_batch}_{imgProfile}2"
    saveSurveyToJson(file_name, result, cellarPath)

    return result



def saveSurveyToJson(file_name, survey, cellarPath):
    """설문 데이터를 JSON 파일로 저장"""
    if os.path.isdir(cellarPath): pass
    else : misc.createFolder(cellarPath)
    json_path = f"{cellarPath}/{file_name}.json"
    
    try:
        with open(json_path, 'w', encoding='utf-8') as json_file:
            json.dump(survey, json_file, ensure_ascii=False, indent=4)
        print(f"[savePlateInfo] Survey data saved to {json_path}")
    except Exception as e:
        print(f"[savePlateInfo] Failed to save survey data: {e}")


def checkSelected(target_dictionary):
    target_cnt = {}
    #print('check selection', target_dictionary)
    for pID in [*target_dictionary]:
        cnt = 0
        for selected_well, target_points in target_dictionary[pID].items():
            if len(target_points) > 0: cnt += 1
        target_cnt[pID] = cnt
    else: pass
    #print('check selection', cnt)
    return target_cnt


def validateWell(string):
    wells = string.split(',')
    newWells = []
    for item in wells:
        try:
            n = int( item.strip()[1:] )
            c = item.strip()[0]
            if n >= 1 and n <= 24:
                #if int(c) >= int(ord('A')) and int(c) <= int(ord('P')):
                if c >= 'A' and c <= 'P':
                    well = '{0}{1}'.format( c,str(n).zfill(2) )
                    newWells.append(well)
                #elif int(c) >= ord('a') and int(c) <= ord('p'):
                elif c >= 'a' and c <= 'p':   
                    well = '{0}{1}'.format( c.upper(),str(n).zfill(2) )
                    newWells.append(well)
                else:
                    print('[ Warning ] Incorrect Well!')
            else:
                print('[ Warning ] Incorrect Well!')
        except ValueError as e:
            print('[ Warning ] Incorrect Well!')
    print(newWells)
    return newWells

def wellsToPrep(libScale, wellVol, cryoVol, userWells):
    grid384 = []
    for i in range(1,25):
        for j in range(65, 81):
            w = '{0}{1}'.format(chr(j),str(i).zfill(2))
            grid384.append(w)
    chambers = math.ceil( float(cryoVol) * libScale / wellVol )
    times = math.trunc( wellVol / float( cryoVol ) )
    if len(userWells) == 0:
        print('[ Warning ] Enter the first well to prepare CryoProtectant')
    else:
        fromWhere = grid384.index(userWells[-1])
        if len(grid384[fromWhere:]) < chambers:
            print('[ Warning ] Prepare Another plate for CryoProtectant')
        else:
            if len(userWells) >= chambers:
                pass
            else:
                fromWhere = grid384.index(userWells[-1])
                while len(userWells) < chambers:
                    #nextWell = chr( ord( userWells[-1][0] ) + 1 ) + userWells[-1][1:]
                    fromWhere += 1
                    nextWell = grid384[fromWhere]
                    userWells.append(nextWell)
                print('needed wells:', chambers, 'userWell++', userWells)
    return userWells


def whichCryoWell(wells, wellVol, cryoVol, cryoSites, cnt):
    times = math.trunc(wellVol / cryoVol) *cryoSites
    try:
        Nth = int( cnt // times )
    except ZeroDivisionError: pass
    return wells[Nth].strip()
    

def createExpID(plantype, expid, protein, explist):
    newid = 'new'
    if expid == 'New' or expid == 'new':
        year = datetime.today().year
        month =  str(datetime.today().month).zfill(2)
        uniq  = 1
        if plantype == 'pretest': prefix = 'PreTest'
        elif plantype == 'screen': prefix = 'FragSC'
        else: prefix == 'free'
        newid = '{0}-{1}{2}-{3}-{4}'.format(prefix, year,month, protein.upper(),str(uniq).zfill(2))
        while newid in explist:
            uniq += 1
            #newid = 'FragSc-{0}{1}-{2}-{3}'.format(year,month, protein.upper(),str(uniq).zfill(2))
            newid = '{0}-{1}{2}-{3}-{4}'.format(prefix, year,month, protein.upper(),str(uniq).zfill(2))
    return newid
#Screen/PreTest/FreeTest
#Evalue/
#Liberty:

def freeTargetsToCSV(platetype, targets):
    echoHeader = ['Source plate name', 'Source Well','Transfer Volume'\
                  'Destination plate name', 'Targeting Well(forView)','Destination Well',\
                  'Destination Well X offset', 'Destination Well Y offset']

    targetList = []
    targetList.append(echoHeader)
    for i, target in enumerate(targets.keys()):
        xtalplate = target.split('_')[0]
        xtalwell  = target.split('_')[1]
        for point in targets[target]:
            if point[0].startswith('free') or point[0].startswith('Free'):
                line = ['', '', '', xtalplate, xtalwell, adpt.transPlate(platetype, xtalwell),\
                                point[1], point[2]]
                targetList.append(line)
            else: pass
    return targetList

def targetsToCSV2(platetype, expid, solvtype, protein, targets, paths, incMinute, conditions):
    username = misc.getUsername()
    prjid = adpt.usernameToID(username)
    echoHeader = ['Source plate name', 'Source Well',\
                  'Destination plate name', 'Targeting Well(forView)','Destination Well',\
                  'Destination Well X offset', 'Destination Well Y offset', 'Transfer Volume']
    shftHeader = [';PlateType', 'PlateID', 'LocationShifter', 'PlateRow', 'PlateColumn', 'PositionSubWell',\
                  'Comment', 'CrystalID', 'TimeArrival', 'TimeDeparture', 'PickDuration',\
                  'DestinationName', 'DestinationLocation', 'Barcode', 'ExternalComment']
    if solvtype == 'DMSO':
        smile = 'CS(=O)C'
    elif solvtype == 'EthyleneGlycol':
        smile = 'C(CO)O'
    else : smile = 'none'
    #chemList = []
    #chemList.append(echoHeader)
    #cryoList = []
    #cryoList.append(echoHeader)
    echoList = []
    echoList.append(['classification']+echoHeader)
    shftList = []
    shftList.append(shftHeader)
    DBrecord = []

    print('we are here with evaluetargets', targets)
    for conKey in sorted(targets.keys()):
        #for i, time in enumerate(incMinute):
        #    if conKey.split(',')[-1] == str(i):
        #        incTime = time
        i = int(conKey.split(',')[0])
        j = int(conKey.split(',')[1])
        solVol = 0
        crpVol = 0
        replica = 1
        for dropKey, points in sorted(targets[conKey].items()):
            destination = dropKey.split('_')
            xtalplate   = destination[0]
            xtalwell    = destination[1]
            xtalRow     = xtalwell[0]
            xtalCol     = str(int(xtalwell[1:-1]))
            xtalSub     = xtalwell[-1]
            shftline = [platetype, xtalplate, 'AM', xtalRow, xtalCol, xtalSub]
            shftList.append(shftline)
            for po in points:
                classification = po[0] #solvent or cryopro 
                if classification.startswith('solv'):
                    sourceplate = ''
                    sourcewell = 'A1'
                    targetwell  = adpt.transPlate(platetype, xtalwell)
                    if xtalwell.endswith('d'): xoffset = po[1] - 700
                    else                     : xoffset = po[1]
                    yoffset = po[2]
                    transfervol = po[-1]
                    if classification == 'solvent': solVol += transfervol
                    else: pass
                    line = [classification, sourceplate, sourcewell, xtalplate, xtalwell,targetwell, xoffset, yoffset, transfervol]
                    print('lines for echo', line)
                    echoList.append(line)
            for po in points:
                classification = po[0] #solvent or cryopro 
                if classification.startswith('cryo'):
                    sourceplate = ''
                    sourcewell = 'A1'
                    targetwell  = adpt.transPlate(platetype, xtalwell)
                    if xtalwell.endswith('d'): xoffset = po[1] - 700
                    else                     : xoffset = po[1]
                    yoffset = po[2]
                    transfervol = po[-1]
                    if classification == 'cryopro': crpVol += transfervol
                    else: pass
                    line = [classification, sourceplate, sourcewell, xtalplate, xtalwell,targetwell, xoffset, yoffset, transfervol]
                    print('lines for echo', line)
                    echoList.append(line)              

            condition = '{0}-{1}'.format(conditions[i][j],replica)
            record = { "name": username, "staff_comments": "upload by XtalViewer", "status": 5, "attachment": "null",\
                       "expri_id": expid, "protein_name": protein, "plate_type": platetype,\
                       "plate_code": xtalplate, "plate_imgpath": "none", "plate_well": xtalwell,\
                       "plate_x": 0, "plate_y": 0, "crystal_no" : i+1,\
                       #Not matter in pretest
                       "soak_plate": 'pretest', "soak_well": 'Z00', "soak_vol": solVol,\
                       "soak_id": condition, "soak_smile": smile, "project_id": prjid }
            DBrecord.append(record)        
            replica += 1
    echoPath = '{0}/{1}'.format(paths['echo650'], username)
    shft1Path = '{0}/{1}'.format(paths['shifter1'], username)
    shft2Path = '{0}/{1}'.format(paths['shifter2'], username)

    misc.listToCsv(echoPath, expid, echoList)
    misc.listToCsv(shft1Path, expid, shftList)
    misc.listToCsv(shft2Path, expid, shftList)

    jsonPath = '{0}/log/{1}'.format(paths['cellar'], expid)
    if os.path.isdir(jsonPath):
        print("remove * json")
    else:
        misc.createFolder(jsonPath)
    print('[Repeat]', len(DBrecord))
    for i,item  in enumerate(DBrecord):
        jsonname = '{0}_{1}'.format(expid, str(i+1).zfill(3))
        misc.jsonWriter(jsonPath, jsonname, item)
        print(jsonname)
        print(i, item)

    return jsonPath, DBrecord



def deliverData(paths, expid, DBrecord):
    jsonPath = '{0}/log/{1}'.format(paths['cellar'], expid)
    if os.path.isdir(jsonPath):
        print("remove * json")
    else:
        misc.createFolder(jsonPath)
    print('[Repeat]', len(DBrecord))
    for i,item  in enumerate(DBrecord):
        jsonname = '{0}_{1}'.format(expid, str(i+1).zfill(3))
        misc.jsonWriter(jsonPath, jsonname, item)
        print(jsonname)
        print(i, item)
    return jsonPath




def targetsToCSV(platetype, expid, explist, protein, targets, paths):
    username = misc.getUsername()
    echoHeader = ['Source plate name', 'Source Well',\
                  'Destination plate name', 'Targeting Well(forView)','Destination Well',\
                  'Destination Well X offset', 'Destination Well Y offset', 'Transfer Volume']
    shftHeader = [';PlateType', 'PlateID', 'LocationShifter', 'PlateRow', 'PlateColumn', 'PositionSubWell',\
                  'Comment', 'CrystalID', 'TimeArrival', 'TimeDeparture', 'PickDuration',\
                  'DestinationName', 'DestinationLocation', 'Barcode', 'ExternalComment'] 
    chemList = []
    chemList.append(echoHeader)
    cryoList = []
    cryoList.append(echoHeader)
    shftList = []
    shftList.append(shftHeader)    
    DBrecord = []

    for i,xtal in enumerate(targets.keys()):
        print("[utils/func] {0}".format(xtal))
        destination = xtal.split('_')
        xtalplate   = destination[0]
        xtalwell    = destination[1] 
        xtalRow     = xtalwell[0]
        xtalCol     = str(int(xtalwell[1:-1]))
        xtalSub     = xtalwell[-1]
        shftline = [platetype, xtalplate, 'AM', xtalRow, xtalCol, xtalSub]
        shftList.append(shftline)

        chemvol = 0
        for shot in targets[xtal]:
            if shot[0].startswith('chem'):
                cheminfo = shot[0].split(' ')
                print(cheminfo)
                sourceplate = cheminfo[2]
                sourcewell  = cheminfo[3]
                transfervol = cheminfo[4]
                targetwell  = adpt.transPlate(platetype, xtalwell)
                xoffset     = shot[1]
                if xtalwell.endswith('d'): yoffset = shot[2] - 700
                else                     : yoffset = shot[2]
                chemvol += float(transfervol)
                line = [sourceplate, sourcewell, xtalplate, xtalwell,targetwell, xoffset, yoffset, transfervol]
                chemList.append(line)
            elif shot[0].startswith('cryo'):
                cryoinfo = shot[0].split(' ')
                sourceplate = cryoinfo[2]
                sourcewell  = cryoinfo[3]
                transfervol = cryoinfo[4]
                xoffset     = shot[1]
                if xtalwell.endswith('d'): yoffset = shot[2] - 700
                else                     : yoffset = shot[2]
                line = [sourceplate, sourcewell, xtalplate, xtalwell,targetwell, xoffset, yoffset, transfervol]
                cryoList.append(line)

            elif shot[0].startswith('p'):
                #['ps', 'plateID', 'plateWell', 'chemID', 'smile', 'imgpath']
                prjid = adpt.usernameToID(username)
                chemplate = shot[1]
                chemwell  = shot[2]
                chemid    = shot[3]
                smile     = shot[4]
                imgpath   = shot[5]
                record = { "name": username, "staff_comments": "upload by XtalViewer", "status": 5, "attachment": "null",\
                           "expri_id": expid, "protein_name": protein, "plate_type": platetype,\
                           "plate_code": xtalplate, "plate_imgpath": imgpath, "plate_well": xtalwell,\
                           "plate_x": 0, "plate_y": 0, "crystal_no" : i+1,\
                           "soak_plate": chemplate, "soak_well": chemwell, "soak_vol": chemvol,\
                           "soak_id": chemid, "soak_smile": smile, "project_id": prjid }
                print("[utils/func] {0}".format(record))
                DBrecord.append(record)
            else: pass
    echoPath = '{0}/{1}'.format(paths['echo650'], username)
    shft1Path = '{0}/{1}'.format(paths['shifter1'], username)
    shft2Path = '{0}/{1}'.format(paths['shifter2'], username)

    misc.listToCsv(echoPath, expid+'_chem', chemList)
    misc.listToCsv(echoPath, expid+'_cryo', cryoList)
    misc.listToCsv(shft1Path, expid, shftList)
    misc.listToCsv(shft2Path, expid, shftList)
    
    jsonPath = '{0}/log/{1}'.format(paths['cellar'], expid)
    if os.path.isdir(jsonPath):
        print("remove * json")
    else:
        misc.createFolder(jsonPath)
    print('[Repeat]', len(DBrecord))
    for i,item  in enumerate(DBrecord):
        jsonname = '{0}_{1}'.format(expid, str(i+1).zfill(3))
        misc.jsonWriter(jsonPath, jsonname, item)
        print(jsonname)
        print(i, item)

    return jsonPath, DBrecord

#func.xtalToWeb(self.xtalsToHarvest, self.NewExpID, sel.proteinName.text(),self.pathway)
    

def shft1Done(DBrecord, expid, protein, paths):
    username = misc.getUsername()
    shft1path = '{0}/{1}'.format(paths['shifter1'], username)
    shft1file = '{0}/{1}'.format(shft1path, misc.latestFile(shft1path, expid, 'csv'))

    shft1done = []
    for i, row in enumerate(misc.csvToList(shft1file)):
        tempDict = {}
        try:
            tempDict['plate_code'] = row[1]
            tempDict['plate_well'] = row[3]+row[4].zfill(2)+row[5]
            tempDict['harvest_time'] = row[9]
            #tempDict['harvest_order'] = row[7]
            tempDict['harvest_order'] = 1 + 1
            tempDict['harvest_comment'] = row[6]
            tempDict['puck_name'] = row[11]
            tempDict['puck_nohole'] = row[12]
            shft1done.append(tempDict)
        except IndexError as e:
            print('[shft1] no record at this well')
            print(e)

    for record in DBrecord:
        if record['expri_id'] == expid:
            for item in shft1done:
                if record['plate_code'] == item['plate_code'] and record['plate_well'] == item['plate_well']:
                    if 'puck_name' not in record:
                        record.update(item)
                        print(record)
                    else:
                        print('Fatal Problem')
                else: pass
        else: pass

    jsonPath = '{0}/log/{1}_done'.format(paths['cellar'], expid)
    if os.path.isdir(jsonPath):
        print("remove * json")
    else:
        misc.createFolder(jsonPath)
    print('[Repeat]', len(DBrecord))
    for i,item  in enumerate(DBrecord):
        jsonname = '{0}_{1}'.format(expid, str(i+1).zfill(3))
        misc.jsonWriter(jsonPath, jsonname, item)
        print(jsonname)
        print(i, item)
    return jsonPath

def shft2Done(DBrecord, expid, protein, paths):
    username = misc.getUsername()
    shft2path = '{0}/{1}'.format(paths['shifter2'], username)
    shft2file = '{0}/{1}'.format(shft2path, misc.latestFile(shft2path, expid, 'csv'))
    
    shft2done = []
    for i, row in enumerate(misc.csvToList(shft2file)):
        tempDict = {}
        try:
            tempDict['plate_code'] = row[1]
            tempDict['plate_well'] = row[3]+row[4].zfill(2)+row[5]
            tempDict['harvest_time'] = row[9]
            #tempDict['harvest_order'] = row[7]
            tempDict['harvest_order'] = 1 + 1
            tempDict['harvest_comment'] = row[6]
            tempDict['puck_name'] = row[11]
            tempDict['puck_nohole'] = row[12]
            shft2done.append(tempDict)
        except IndexError as e:
            print('[shft2] no record at this well')
            print(e)
         
    for record in DBrecord:
        if record['expri_id'] == expid:
            for item in shft2done:
                if record['plate_code'] == item['plate_code'] and record['plate_well'] == item['plate_well']:
                    if 'puck_name' not in record:
                        record.update(item)
                        print(record)
                    else:
                        print('Fatal Problem')
                else: pass
        else: pass
    
    jsonPath = '{0}/log/{1}_done'.format(paths['cellar'], expid)
    if os.path.isdir(jsonPath):
        print("remove * json")
    else:
        misc.createFolder(jsonPath)
    print('[Repeat]', len(DBrecord))
    for i,item  in enumerate(DBrecord):
        jsonname = '{0}_{1}'.format(expid, str(i+1).zfill(3))
        misc.jsonWriter(jsonPath, jsonname, item)
        print(jsonname)
        print(i, item)
    return jsonPath


#def chemPlanWarning(lib, protein, chemvol, cryovol, chemsite, cryosite)


def setLabel(img, pts, label):
    (x,y,w,h) = cv2.boundingRect(pts)
    pt1 = (x,y)
    pt2 = (x+w, y+h)
    cv2.rectangle(img, pt1, pt2, (0,255,0),2)
    cv2.putText(img, label, (pt1[0], pt1[1]-3), cv2.FONT_HERSHEY_SIMPLEX, 0.7,(0,0,255))

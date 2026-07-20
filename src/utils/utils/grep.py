import os, sys
import numpy as np
import traceback
from datetime import datetime

from utils import misc, adpt

def pidToRmPath(plateid, storagepath):
    pid = int( plateid )
    if pid <=  999 : platepath = '{0}/{1}/plateID_{1}'.format(storagepath, str(pid) )
    else           : platepath = '{0}/{1}/plateID_{2}'.format(storagepath, str(pid)[1:], str(pid) ) 
    return platepath

def checkPlate(platelist, storagepath):
    #platelist = misc.readRange(platestr)
    validPlates= []
    for pid in platelist:
        platepath = pidToRmPath(pid, storagepath)
        #lastpath  = platepath + '/' + misc.latestDir(pIDpath, 'batchID')
        if os.path.exists(platepath):
            validPlates.append(pid)
        else: pass
    return validPlates

def surveyPlate(validplates, platetype, imgprofile, storagepath):
    if platetype == 'SwissCI-MRC-3d':
        plateGrid = {"row" : ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'], \
                     "col" : [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], \
                     "baby": ['a','c','d']}
    elif platetype == 'SwissCI-MRC-2d':
        plateGrid = {"row" : ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'], \
                     "col" : [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], \
                     "baby": ['a','b']} #I will change it to 'c', 'd' someday.
    else: pass
 
    saminfo = ['nu', 'plateID', 'Well', 'WellNo', 'ImgCreaged', 'IMGpath'] 
    platesinfo = {}
    plategrids   = {}
    for pid in validplates:
        platepath = pidToRmPath(pid, storagepath)
        lastbatch  = platepath + '/' + misc.latestDir(platepath, 'batchID')
        print(lastbatch)
        #lastpath를 판단 기준으로 사용할때, 없는 플레이트 번호를 넣으면 어떤 에러가 나오는지 확인 후 예외처리I
        platesinfo[pid] = {}
        dropplate = np.zeros( (16, 24) )
        wellno = {'rms':0, 'total':0, 'absent':0}
        if os.path.exists(lastbatch):
            platesinfo[pid] = []
            for row in plateGrid['row']:
                for col in plateGrid['col']:
                    wellno['rms'] += 1
                    wellpath = '{0}/wellNum_{1}/{2}'.format(lastbatch, wellno['rms'], imgprofile)
                    if os.path.isdir(wellpath):
                        for i, subwell in enumerate(plateGrid['baby']):
                            wellno['total'] += 1
                            well = '{0}{1:02d}{2}'.format(row, col, subwell)
                            drop = 'd{0}'.format(i+1)
                            imgs = [item for item in sorted(os.listdir(wellpath)) if item.startswith(drop) and item.endswith('ef.jpg')]
                            if len(imgs) == 0: wellno['absent'] += 1
                            else:
                                imgname = imgs[-1]
                                imgpath = '{0}/{1}'.format(wellpath, imgname)
                                imgctime = datetime.fromtimestamp( os.path.getctime(imgpath) )      
                                well384 = adpt.transPlate(platetype,'Labcyte384', well)
                                dropdict = {'platetype':platetype, 'rmw':wellno['rms'], 'well':well, 'well384':well384, 'ctime':imgctime, 'imgpath':imgpath}
                                platesinfo[pid].append(dropdict)
                                #except (IndexError, OSError) as e:
                                #    wellno['absent'] += 1
                                #    traceback.print_exc()
                                #except: traceback.print_exc()
                                grid_row = ( ord(well384[0].upper()) - ord('A') + 1 ) -1
                                grid_col = ( int(well384[1:]) ) -1
                                dropplate[grid_row][grid_col] = 1
                                plategrids[pid] = dropplate
                    else: pass
            #print(dropplate)
        print('[surveyPlate] pID_{0} has {1}/{2}drops'.format( pid, wellno['total']-wellno['absent'], wellno['total'] ))
    return platesinfo, plategrids

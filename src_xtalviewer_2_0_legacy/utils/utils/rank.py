import os, sys
import glob
import getpass, numpy, cv2
import matplotlib.pyplot as plt
from PIL import Image
from skimage import color, util, measure, morphology
#from scipy.ndimage import label
#import scipy.optimize
import scipy
import scipy.stats 
#from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtWidgets import *
#from PyQt5.QtGui import QIcon, QImage, QPixmap, QPalette, QPainter
from PyQt5.QtGui import *
from PyQt5.QtPrintSupport import QPrintDialog, QPrinter
#from PyQt5.QtCore import Qt
from PyQt5.QtCore import *

import misc, imgm

class GenBackdropImg(QWidget):

    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        self.setWindowTitle('Generate Backdrop Image')
        self.move(300, 300)
        self.resize(500, 400)
        self.show()

        self.pathway = {'cellar'  :'/data/users/{0}/FBDD'.format(misc.getUsername()),\
                        'rmserver':'/smbmount/rmserver/RockMakerStorage/WellImages',\
                        'echo650' :'/smbmount/echo650',\
                        'shifter1':'/smbmount/shifter1',\
                        'shifter2':'/smbmount/shifter2'}
        self.accpath = {'icons'   :'/usr/local/XtalViewer/1.0/img/icons'}
        self.backdropImgDict = {}


        self.objPID = QLineEdit('318')
        self.imgPaths = {'a':[], 'b':[], 'c':[]}
        self.currImgPath = 'none'
        initArray = numpy.random.rand( 375,450 )
        _max = initArray.max()
        _min = initArray.min()
        _arr = numpy.uint8(255 * ((numpy.float32(initArray) - _min)) / (_max - _min))
        print( _arr.shape[1], _arr.shape[0] )
        _img = QImage(_arr.data, _arr.shape[1], _arr.shape[0], _arr.shape[1], QImage.Format_Alpha8)
        _pix = QPixmap.fromImage(_img)

        blancArray = numpy.ones( (125,150) )
        #_max = initArray.max()
        #_min = initArray.min()
        _arrBlanc = numpy.uint8(100 * ((numpy.float32(blancArray) - _min)) / (_max - _min))
        _imgBlanc = QImage(_arrBlanc.data, _arrBlanc.shape[1],_arrBlanc.shape[0],_arrBlanc.shape[1],QImage.Format_Alpha8)
        _pixBlanc = QPixmap.fromImage(_imgBlanc)

        #self.babyAaccount = QLabel("0 images selected\nfor subwell A")
        #self.babyBaccount = QLabel("0 images selected\nfor subwell B")
        #self.babyCaccount = QLabel("0 images selected\nfor subwell C")
        #self.babyAaccount.setAlignment(Qt.AlignCenter)
        #self.babyBaccount.setAlignment(Qt.AlignCenter)
        #self.babyCaccount.setAlignment(Qt.AlignCenter)

        self.scoreBoard = QLabel('score view')
        self.orgImgLabel = QLabel()
        self.trsImgLabel  = QLabel()
        self.bdpImgLabel = QLabel()

        self.orgImgLabel.setPixmap(_pix)
        self.trsImgLabel.setPixmap(_pixBlanc)
        self.bdpImgLabel.setPixmap(_pixBlanc)

        self.orgImgPixmap = QPixmap()
        self.trsImgPixmap  = QPixmap()
        self.bdpImgPixmap = QPixmap()


    #def loadPlate(self):
        grid = QGridLayout()

        load = QPushButton('Load')
        load.clicked.connect( self.grepImages )
        rank = QPushButton('Rank')
        rank.clicked.connect( self.zero )
        grid.addWidget(QLabel('PlateID To Ranking'),  0,0)
        grid.addWidget(self.objPID,                   0,1)
        grid.addWidget(load,                          0,2)
        grid.addWidget(rank,                          0,3)
        grid.addWidget(self.Viewer(),             1,0,1,4)
        #grid.addWidget(save,                     2,0,1,3)
        self.setLayout(grid)
        

        return grid

    def Viewer(self):
        groupbox = QGroupBox()
        grid = QGridLayout(groupbox)

        grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        grid.addWidget(self.orgImgLabel,   0,0,3,3)
        grid.addWidget(self.trsImgLabel,   0,4,1,1)
        grid.addWidget(self.trsImgLabel,   1,4,1,1)
        grid.addWidget(self.bdpImgLabel,   2,4,1,1)

 
        groupbox.setLayout(grid)
        return groupbox

    def Control(self):
        groupbox = QGroupBox()
        grid = QGridLayout(groupbox)
        
        homeButton = QPushButton()
        prevButton = QPushButton()
        nextButton = QPushButton()
        lastButton = QPushButton()

        homeButton.clicked.connect(self.moveFirstWell)
        prevButton.clicked.connect(self.movePrevWell)
        nextButton.clicked.connect(self.moveNextWell)
        lastButton.clicked.connect(self.moveLastWell)

        homeButton.setIcon(QIcon('{0}/{1}_first.png'.format(self.accpath['icons'],self.iconColor)))
        prevButton.setIcon(QIcon('{0}/{1}_prev.png'.format(self.accpath['icons'],self.iconColor)))
        nextButton.setIcon(QIcon('{0}/{1}_next.png'.format(self.accpath['icons'],self.iconColor)))
        lastButton.setIcon(QIcon('{0}/{1}_end.png'.format(self.accpath['icons'],self.iconColor)))
  
        grid.addWidget(homeButton, 0,1)
        grid.addWidget(prevButton, 0,2)
        grid.addWidget(nextButton, 0,3)
        grid.addWidget(lastButton, 0,4)




    def grepImages(self):
        rmserver = self.pathway['rmserver']
        _pID = str(self.objPID.text()).split(',')
        pID = [ item.strip() for item in _pID ][0]
        realPlates = sorted(os.listdir(rmserver))
        print(pID, realPlates)
        if pID :
            try:
                realPlates = sorted(os.listdir(rmserver))
                if pID in realPlates:
                    pIDpath     = "{0}/{1}/plateID_{1}".format(rmserver, pID)
                    pIDlatePath = "{0}/{1}".format(pIDpath, misc.latestDir(pIDpath, 'batchID'))
                    print('[Load Plate] pID{0} found in RMServer'.format(pID))
                    print('[Load Plate] Latest Path: {0}'.format(pIDlatePath))
                    for (path, dir, files) in os.walk(pIDlatePath):
                        for filename in files:
                            filepath = "{0}/{1}".format(path, filename)
                            if filename.startswith('d1') and filename.endswith('ef.jpg'):
                                self.imgPaths['a'].append(filepath)
                            elif filename.startswith('d2') and filename.endswith('ef.jpg'):
                                self.imgPaths['b'].append(filepath)
                            elif filename.startswith('d3') and filename.endswith('ef.jpg'):
                                self.imgPaths['c'].append(filepath)
                            else: pass
                else:
                    print("Invalid plate ID")
            except:
                pass
        else: pass
        self.currImgPath = self.imgPaths['a'][0]
        self.dispImage(self.currImgPath)
        self.loadGrounds() 
        #FileNotFoundError: [Errno 2] 그런 파일이나 디렉터리가 없습니다: '/usr/local/XtalViewer/1.0/data/BackdropImages.npz' << can be occurred
        #ValueError: Cannot load file containing pickled data when allow_pickle=False << can be occurred 

    def loadGrounds(self):
        backdropImages = numpy.load('/usr/local/XtalViewer/1.0/data/BackdropImages.npz')
        print("[LoadGround] Load BackDrop Images (gray/binary) as Array")
        DictIndex = ['aa', 'ab', 'ba', 'bb', 'ca', 'cb']
        for item in sorted(DictIndex):
            self.backdropImgDict[item] = backdropImages[item]
            print(self.backdropImgDict[item].shape[1], self.backdropImgDict[item].shape[0] )
            print(self.backdropImgDict[item])
        backdropImages.close() 
        #self.backdropImgDict = { 'aa': arr_0, 'ab': arr_1, \
        #                         'ba': arr_2, 'bb': arr_3, \
        #                         'ca': arr_3, 'cb': arr_5 }
        

    def dispImage(self, currImgPath):
        imgpath = self.currImgPath
        image = QImage(imgpath)
        width = 450
        height = image.height()* (width/image.width())
        self.orgImgLabel.setFixedWidth(width)
        self.orgImgLabel.setFixedHeight(height)
        self.orgImgPixmap = QPixmap(image)
        self.orgImgPixmap = self.orgImgPixmap.scaledToWidth(width)
        self.orgImgPixmap = self.orgImgPixmap.scaledToHeight(height)
        self.orgImgLabel.setPixmap(self.orgImgPixmap)
    #def dispTImg(self, self.currImgPath):
    #def dispBDIm(self, self.currImgPath):
   

    def calcImage(self):
        subwell = ['a','b','c']
        TranslateFlag = 0
        TranslateVectors = numpy.zeros( (int(len(subwell)), int(3)) )
        centroids = []
        FD_features = []
        #se = strel('disk', 3)
        #se2 = strel('disk', 5)
        se  = morphology.disk(3)
        se2 = morphology.disk(5)
        histGradBinCentre = numpy.linspace(0,5,50)
        histGradBinCentre = numpy.concatenate((histGradBinCentre,[numpy.inf]))
        for well in subwell:
            imAveSmall = self.backdropImgDict[well+'a'] #'aa', 'ba', 'ca'
            bwSmall    = self.backdropImgDict[well+'b'] #'ab', 'bb', 'cb'
            for i, imgpath in enumerate(self.imgPaths[well]):
                wellno = i + 1
                currsub = i % len(subwell) #0, 1, 2
                image = Image.open(imgpath)
                _arr  = numpy.array(image)
                _arr2 = color.rgb2gray(util.invert(_arr))
                _arr3 = imgm.imadjust(_arr2, _arr2.min(), _arr2.max(), 0,1)
                _arrS = numpy.resize(_arr3, (125,150))
                imSmall = _arrS
                if wellno > len(subwell):
                    t = TranslateVectors[currsub]
                else:
                    if numpy.mean(numpy.mean(_arr3, axis=0)) < 111:
                        #Matlab sentence TO TRANSLATE
                        #t(1) = 0
                        #t(2) = 0
                        #t(3) = 1 Lets regard the t as list
                        t = [0, 0, 1]
                    else:
                        f = lambda x: (numpy.sum((rank_fx.translate_image(imAveSmall,x) - imSmall)**2))**(0.5)
                        if TranslateFlag == 0:
                            t = [-5, 3, 1]
                        else: pass
                            #f = lambda t: (numpy.sum((imgm(imAveSmall,t) - imSmall)**2))**(0.5)    
                        t = scipy.optimize.fmin(func=f, x=t)
                        TranslateVectors[currsub] = t
                        TranslateFlag = 1
                 
                bwT = rank_fx.translate_image(bwSmall, t)
                bwTer = cv2.erode(bwT, se)
                bwTer[0]    = 0
                bwTer[-1]   = 0
                bwTer[:,0]  = 0
                bwTer[:,-1] = 0
                if wellno > len(subwell)+1:
                    temp = measure.regionprops( scipy.misc.imresize(bwTer, _arr3.shape, 'nearest') 
                    centroids.append( temp.Centroid )
     
                MaskedIm = numpy.multiply(bwTer, imSmall)
                temp = numpy.zeros_like(MaskedIm)
                #idx = numpy.zeros_like(MaskedIm)
                #idx[ MaskedIm > 0 ] = 1
                idx = MaskedIm > 0
                #meanMI = MaskedIm.sum() / (MaskedIm>0).sum()
                mStd = ( (MaskedIm[idx].std()**2)*len(MaskedIm[idx])/(len(MaskedIm[idx])-1) )**0.5
                temp = (MaskedIm - MaskedIm[idx].mean()) / mStd
                MaskedIm = temp                
                
                [fy,fx] = numpy.gradient(MaskedIm)
                gradIm = numpy.multiply( cv2.erode(bwT,se2), (abs(fx)+abs(fy)))
                gradIm[0:2] = 0
                gradIm[-2:] = 0
                idxGrad = gradIm > 0
                aveGrad = gradIm[idxGrad].mean()
                mStdGrad = ( (GradIm[idxGrad].std()**2)*len(GradIm[idxGrad])/(len(GradIm[idxGrad])-1) )**0.5
                skewGrad = scipy.stats.skew(gradIm[idxGrad])
                kurtGard = scipy.stats.kurtosis(gradIm[idxGrad], fisher=False)
                DistributionGrad = numpy.histogram(gradIm[idxGrad], histGradBinCentre)
                _DistributionGard = DistributionGrad / sum(DistributionGrad)
                DistributionGrad = _DistributionGrad
                
                FD_features.append([wellno, aveGrad, mStdGrad, skewGrad, kurtGard, DistributionGrad])


            for 


    def averageImg(self, imgPaths):
        firstImg = QImage(imgPaths[0])
        blancArr = numpy.zeros( (int(firstImg.height()), int(firstImg.width())) )
        imgSum = blancArr
        #imgAve = blancArr
        for imgpath in imgPaths :
            print(imgpath)
            image = Image.open(imgpath)
            _arr  = numpy.array(image)
            _arr2 = color.rgb2gray(util.invert(_arr))
            _arr3 = imgm.imadjust(_arr2, _arr2.min(), _arr2.max(), 0,1)
            imgSum = imgSum + _arr3
        _arrAve = imgSum / int(len(imgPaths))
        _max = _arrAve.max()
        _min = _arrAve.min()
        arrAve = numpy.uint8(255 * ((numpy.float32(_arrAve) - _min)) / (_max - _min))
        return arrAve

    def binaryImg(self, arr, threshold):
        _th = numpy.zeros_like(arr)
        _th[ arr > threshold ] = 255
        #_img = QImage(_th.data, _th.shape[1], _th.shape[0], _th.shape[1], QImage.Format_Alpha8)
        #_pix = QPixmap.fromImage(_img)
        return _th

    def arrToImg(self, arr):
        _arr = arr
        _img = QImage(_arr.data, _arr.shape[1], _arr.shape[0], _arr.shape[1], QImage.Format_Alpha8)
        _pix = QPixmap.fromImage(_img)
        return _pix

    #def imgToArr(self, im):
        

    def zero(self):
        print("I'm not anything")

if __name__ == '__main__':
   app = QApplication(sys.argv)
   ex = GenBackdropImg()
   sys.exit(app.exec_())



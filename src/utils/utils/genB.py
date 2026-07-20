import os, sys
import glob
import getpass, numpy, cv2
import matplotlib.pyplot as plt
from PIL import Image
from skimage import color, util, measure
from scipy.ndimage import label
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

        self.zeroPID = QLineEdit('700')
        self.imgPaths = {'a':[], 'b':[], 'c':[]}
        
        initArray = numpy.random.rand(125,150)
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

        self.babyAaccount = QLabel("0 images selected\nfor subwell A")
        self.babyBaccount = QLabel("0 images selected\nfor subwell B")
        self.babyCaccount = QLabel("0 images selected\nfor subwell C")
        self.babyAaccount.setAlignment(Qt.AlignCenter)
        self.babyBaccount.setAlignment(Qt.AlignCenter)
        self.babyCaccount.setAlignment(Qt.AlignCenter)
        #self.aveArrA = [[0]]
        #self.aveArrB = [[0]]
        #self.aveArrC = [[0]]
        #self.binArrA = [[0]]
        #self.binArrB = [[0]]
        #self.binArrC = [[0]]
        self.arrays = {}
        self.babyAimgLabel = QLabel()
        self.babyBimgLabel = QLabel()
        self.babyCimgLabel = QLabel()
        self.sumbAimgLabel = QLabel()
        self.sumbBimgLabel = QLabel()
        self.sumbCimgLabel = QLabel()

        self.babyAimgLabel.setPixmap(_pix)
        self.babyBimgLabel.setPixmap(_pix)
        self.babyCimgLabel.setPixmap(_pix)
        self.sumbAimgLabel.setPixmap(_pixBlanc)
        self.sumbBimgLabel.setPixmap(_pixBlanc)
        self.sumbCimgLabel.setPixmap(_pixBlanc)

        self.babyApixmap = QPixmap()
        self.babyBpixmap = QPixmap()
        self.babyCpixmap = QPixmap()
        self.babyApixmapsum = QPixmap()
        self.babyBpixmapsum = QPixmap()
        self.babyCpixmapsum = QPixmap()


    #def loadPlate(self):
        grid = QGridLayout()

        load = QPushButton('Load')
        load.clicked.connect( self.grepImages )
        save = QPushButton('Save All Backdrop Images')
        save.clicked.connect( self.saveImages )

        grid.addWidget(QLabel('Zero PlateID'),   0,0)
        grid.addWidget(self.zeroPID,             0,1)
        grid.addWidget(load,                     0,2)
        grid.addWidget(self.Viewer(),            1,0,1,3)
        grid.addWidget(save,                     2,0,1,3)
        self.setLayout(grid)
        

        return grid

    def Viewer(self):
        groupbox = QGroupBox()
        grid = QGridLayout(groupbox)

        self.thresA = QSlider(Qt.Horizontal)
        self.thresB = QSlider(Qt.Horizontal)
        self.thresC = QSlider(Qt.Horizontal)
        self.thresA.setRange(0,255)
        self.thresA.move(1,1)
        self.thresA.setTickPosition(QSlider.TicksBothSides)
        self.thresA.setTickInterval(17)
        self.thresA.setSingleStep(5)
        self.thresA.setValue(125)
        self.thresA.sliderReleased.connect(self.editThresA)
        self.thresB.setRange(0,255)
        self.thresB.move(1,1)
        self.thresB.setTickPosition(QSlider.TicksBothSides)
        self.thresB.setTickInterval(17)
        self.thresB.setSingleStep(5)
        self.thresB.setValue(125)
        self.thresB.sliderReleased.connect(self.editThresB)
        self.thresC.setRange(0,255)
        self.thresC.move(1,1)
        self.thresC.setTickPosition(QSlider.TicksBothSides)
        self.thresC.setTickInterval(17)
        self.thresC.setSingleStep(5)
        self.thresC.setValue(125)
        self.thresC.sliderReleased.connect(self.editThresC)

        calcA = QPushButton('Calculate')
        calcB = QPushButton('Calculate')
        calcC = QPushButton('Calculate')
        calcA.clicked.connect( self.calcImageA )
        calcB.clicked.connect( self.calcImageB )
        calcC.clicked.connect( self.calcImageC )

        maskA = QPushButton('Mask regions')
        maskB = QPushButton('Mask regions')
        maskC = QPushButton('Mask regions')
        maskA.clicked.connect( self.zero )
        maskB.clicked.connect( self.zero )
        maskC.clicked.connect( self.zero )

        grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        grid.addWidget(self.babyAaccount,  0,0)
        grid.addWidget(self.babyBaccount,  0,1)
        grid.addWidget(self.babyCaccount,  0,2)
        grid.addWidget(self.babyAimgLabel, 1,0)
        grid.addWidget(self.babyBimgLabel, 1,1)
        grid.addWidget(self.babyCimgLabel, 1,2)
        grid.addWidget(self.sumbAimgLabel, 2,0)
        grid.addWidget(self.sumbBimgLabel, 2,1)
        grid.addWidget(self.sumbCimgLabel, 2,2)
        grid.addWidget(self.thresA,        3,0)
        grid.addWidget(self.thresB,        3,1)
        grid.addWidget(self.thresC,        3,2)
        grid.addWidget(calcA,              4,0)
        grid.addWidget(calcB,              4,1)
        grid.addWidget(calcC,              4,2)
        grid.addWidget(maskA,              5,0)
        grid.addWidget(maskB,              5,1)
        grid.addWidget(maskC,              5,2)

 
        groupbox.setLayout(grid)
        return groupbox


    def grepImages(self):
        rmserver = self.pathway['rmserver']
        pID = str(self.zeroPID.text())
        realPlates = sorted(os.listdir(rmserver))
        #print(pID, realPlates)
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

        self.loadImageA()
        self.loadImageB()
        self.loadImageC()


    def loadImageA(self):
        imgpath = self.imgPaths['a'][-1]
        image = QImage(imgpath)
        width = 150
        height = image.height()* (width/image.width())
        self.babyAimgLabel.setFixedWidth(width)
        self.babyAimgLabel.setFixedHeight(height)
        self.babyApixmap = QPixmap(image)
        self.babyApixmap = self.babyApixmap.scaledToWidth(width)
        self.babyApixmap = self.babyApixmap.scaledToHeight(height)
        self.babyAimgLabel.setPixmap(self.babyApixmap)
        account = "{0} images selected\nfor subwell A".format(len(self.imgPaths['a']))
        self.babyAaccount.setText(account) 

    def loadImageB(self):
        imgpath = self.imgPaths['b'][-1]
        image = QImage(imgpath)
        width = 150
        height = image.height()* (width/image.width())
        self.babyBimgLabel.setFixedWidth(width)
        self.babyBimgLabel.setFixedHeight(height)
        self.babyBpixmap = QPixmap(image)
        self.babyBpixmap = self.babyBpixmap.scaledToWidth(width)
        self.babyBpixmap = self.babyBpixmap.scaledToHeight(height)
        self.babyBimgLabel.setPixmap(self.babyBpixmap)
        account = "{0} images selected\nfor subwell B".format(len(self.imgPaths['b']))
        self.babyBaccount.setText(account)

    def loadImageC(self):
        imgpath = self.imgPaths['c'][-1]
        image = QImage(imgpath)
        width = 150
        height = image.height()* (width/image.width())
        self.babyCimgLabel.setFixedWidth(width)
        self.babyCimgLabel.setFixedHeight(height)
        self.babyCpixmap = QPixmap(image)
        self.babyCpixmap = self.babyCpixmap.scaledToWidth(width)
        self.babyCpixmap = self.babyCpixmap.scaledToHeight(height)
        self.babyCimgLabel.setPixmap(self.babyCpixmap)
        account = "{0} images selected\nfor subwell C".format(len(self.imgPaths['c']))
        self.babyCaccount.setText(account)

    def calcImageA(self):
        imgPaths = self.imgPaths['a']
        self.aveArrA = self.averageImg(imgPaths)
        #self.binArr = self.binaryImg(self.aveArr, self.thresA.value())
        #img = self.arrToImg(self.binArr).scaledToWidth(150)
        #self.sumbAimgLabel.setPixmap(img)
        self.arrays['aa'] = self.aveArrA
        self.editThresA()
    def editThresA(self):
        self.binArrA = self.binaryImg(self.aveArrA, self.thresA.value())
        img = self.arrToImg(self.binArrA).scaledToWidth(150)
        self.sumbAimgLabel.setPixmap(img)
        self.arrays['ab'] = self.binArrA
        """
        labeled, nr_obj = label(self.binArr > 200)
        props = measure.regionprops(labeled)
        for i in props:
            print("[Props]")
            print(i.area, i.bbox_area, i.bbox)
            print(i.image)
        print(props[1].image)
        labeled = props[1].image*1
        _max = labeled.max()
        _min = labeled.min()
        _arr = numpy.uint8(255 * ((numpy.float32(labeled) - _min)) / (_max - _min))
        _img = self.arrToImg(_arr)
        self.sumbBimgLabel.setPixmap(_img.scaledToWidth(150))
        """
    def calcImageB(self):
        imgPaths = self.imgPaths['b']
        self.aveArrB = self.averageImg(imgPaths)
        self.arrays['ba'] = self.aveArrB
        #self.binArr = self.binaryImg(self.aveArr, self.thresB.value())
        #img = self.arrToImg(self.binArr).scaledToWidth(150)
        #self.sumbBimgLabel.setPixmap(img)
        self.editThresB()
    def editThresB(self):
        self.binArrB = self.binaryImg(self.aveArrB, self.thresB.value())
        img = self.arrToImg(self.binArrB).scaledToWidth(150)
        self.arrays['bb'] = self.binArrB
        self.sumbBimgLabel.setPixmap(img)

    def calcImageC(self):
        imgPaths = self.imgPaths['c']
        self.aveArrC = self.averageImg(imgPaths)
        self.arrays['ca'] = self.aveArrC
        #self.binArr = self.binaryImg(self.aveArr, self.thresC.value())
        #img = self.arrToImg(self.binArr).scaledToWidth(150)
        #self.sumbCimgLabel.setPixmap(img)
        self.editThresC()
    def editThresC(self):
        self.binArrC = self.binaryImg(self.aveArrC, self.thresC.value())
        img = self.arrToImg(self.binArrC).scaledToWidth(150)
        self.arrays['cb'] = self.binArrC
        print(type(self.arrays['ca']))
        self.sumbCimgLabel.setPixmap(img)
        print(self.arrays)


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

    def saveImages(self):
        print('save button') 
        print(self.arrays)
        #numpy.savez('/usr/local/XtalViewer/1.0/data/BackdropImages', self.aveArrA, self.binArrA,\
        #                                                         self.aveArrB, self.binArrB,\
        #                                                         self.aveArrC, self.binArrC)
        numpy.savez('/usr/local/XtalViewer/1.0/data/BackdropImages', aa = self.arrays['aa'], ab = self.arrays['ab'],\
                                                                 ba = self.arrays['ba'], bb = self.arrays['bb'],\
                                                                 ca = self.arrays['ca'], cb = self.arrays['cb'])

    
    def zero(self):
        print("I'm not anything")

if __name__ == '__main__':
   app = QApplication(sys.argv)
   ex = GenBackdropImg()
   sys.exit(app.exec_())



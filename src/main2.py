# -*- coding: utf-8 -*-
import os, sys
import glob
import getpass
#from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtWidgets import *
#from PyQt5.QtGui import QIcon, QImage, QPixmap, QPalette, QPainter
from PyQt5.QtGui import *
from PyQt5.QtPrintSupport import QPrintDialog, QPrinter
#from PyQt5.QtCore import Qt
from PyQt5.QtCore import *
import PyQt5.Qt

import csv, math, time
import numpy as np
#import matplotlib.pyplot as plt
from datetime import datetime
#from mpl_toolkits.mplot3d import Axes3D
#from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from utils import misc, func, chem, adpt
from utils.client import getMxlive, putMxlive


XTALVIEWER_PATH = '/usr/local/XtalViewer/2.0'
USERHOME_PATH   = '/data/users/{0}'.format(misc.getUsername())
print(USERHOME_PATH)

class index(QMainWindow):

    def __init__(self):
        super().__init__()
        main = MyApp(self)
        self.setCentralWidget(main)

        self.setWindowTitle('Xtal Viewer')
        self.setWindowIcon(QIcon(XTALVIEWER_PATH+'img/fairy_03.png'))
        self.weight = 1200
        self.height = 800
        self.left = 500
        self.top  = 0
        self.setGeometry(self.left, self.top, self.weight, self.height)


        self.statusbar = self.statusBar()
        self.setMouseTracking(True)


        #Enrich Statusbar : 20221011
        #self.statusBar().reformat()
        message = "Hello {} :)".format(misc.getUsername().upper())
        self.distance = QLabel('   0 um')
        self.nWells   = QLabel('0'.rjust(4,' '))
        self.statusBar().showMessage(message)
        self.statusBar().setStyleSheet('border: 0; background-color: #FFF8DC;')
        self.statusBar().setStyleSheet("QStatusBar::item {border: none;}") 
        self.statusBar().addPermanentWidget(VLine())
        self.statusBar().addPermanentWidget(QLabel('distance:'))
        self.statusBar().addPermanentWidget(self.distance)
        self.statusBar().addPermanentWidget(VLine())
        self.statusBar().addPermanentWidget(QLabel('selected well'))
        self.statusBar().addPermanentWidget(self.nWells)
        #self.statusBar().addPermanentWidget(VLine())







        self.show()

class VLine(QFrame):
    # a simple VLine, like the one you get from designer
    def __init__(self):
        super(VLine, self).__init__()
        self.setFrameShape(self.VLine|self.Sunken)


class MyApp(QWidget):

    def __init__(self, window=None):
        super().__init__()
        self.initUI()
        self.window = window



    def closeEvent(self, event):
        pass

    def initUI(self):
        self.mainWidget = QWidget()
        self.mainWidget.closeEvent = self.closeEvent        

        ###[General Definitions]
        self.pathway      = { 'rmserver' : '/smbmount/rmserver/RockMakerStorage/WellImages',\
                              'echo650'  : '/smbmount/echo650',       \
                              'shifter1' : '/smbmount/shifter1',      \
                              'shifter2' : '/smbmount/shifter2',      \
                              'library'  : XTALVIEWER_PATH + '/chems', \
                              'cellar'   : USERHOME_PATH + '/FBDD/record' }
        self.accpath      = { 'icons'    : XTALVIEWER_PATH+'/img/icons'}



        ### [Reading Image Configuration]
        self.ImageTypeDic = {'Visible':'profileID_1', 'Contrast':'profileID_3?', 'HighRes':'profileID_5', 'Polarize':'profileID_8'}
        self.plateGrid    = {"row" : ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'], \
                             "col" : [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], \
                             "baby": ['a','c','d']}
        self.ImageType    = QComboBox(self)
        self.PlateType    = QComboBox(self)


        ### [Take The Well Position]
        self.wellRadius  = 1400       #A BabyDrop Radius  1.4mm (1400um)
        self.guideRadius = 485        #Radius of Centering Guide Circle (485px)
        self.guideShift0 = [150, 50]
        self.guideShift1 = [150, 50]
        self.colOffset = [0,0]  #To Calibrate Horizontal direction motor error ex.[5.2, -1.7]
        self.rowOffset = [0,0]  #To Calibrate  Vertical  direction motor error ex.[2.2, 3.7]
        #30 = 5nl, 45 = 10nl
        #self.pointRadius = 30*2 /(self.imgScale.value()/100)
        self.xCenter  = QSlider(Qt.Horizontal)
        self.yCenter  = QSlider(Qt.Horizontal)
        self.imgScale = QDoubleSpinBox()
        self.restoreButton = QPushButton('Restore')
        self.fixCurrButton = QPushButton('Save')
        self.restoreButton.clicked.connect( self.defaultScreenSetting )
        self.fixCurrButton.clicked.connect( self.fixScreenSetting )
        

        ####Variables for [Save Personalized Configuration]
        self.ConfigPath    = USERHOME_PATH + '/FBDD/config'
        self.screenConfig0 = {'xShift':0, 'yShift':0, 'imgScale':50}
        self.screenConfig1 ={}
        self.OrgImage      = [0,0]

        ###Variables for [Object Plates]
        self.objectPlates = QLineEdit()
        self.tableview = QTableView()
        self.tableview_model = QStandardItemModel()
        self.tableview.setSortingEnabled(True)
        self.checkedPlates = []
        self.plateCatalogue = {}


        ###Variables for [Plate Navigator]
        self.currentPlate = QLineEdit('none')
        self.currentWell  = QLineEdit()
        self.lastWell     = ''                #to restore when user give wrong well name to self.currentWell
        self.currPlate_info = []
        self.currWellNo  = 1
        self.homeButton = QPushButton()
        self.prevButton = QPushButton()
        self.nextButton = QPushButton()
        self.lastButton = QPushButton()
        self.undoButton = QPushButton()
        self.eraserFlag = False
        self.scale = []
        

        ###Variables for [check duration methods]
        self.btnCheckEvalueMethod = QPushButton("Method Duration")
        self.btnCheckScreenMethod = QPushButton("Method Duration")
        self.btnCheckCryoMethod   = QPushButton("Method Duration")


        ###Common Vartiables for all type experiments
        self.crystname = QLineEdit()
        self.divisions = QSpinBox()

        ###Variables for [evaluatePlans]
        #self.crystname = QLineEdit()
        self.solvents  = QLineEdit("DMSO, EG")
        self.incuTime  = QLineEdit("0, 1h, 2h, 6h")
        self.solventVol= QLineEdit("5, 15, 30")
        self.cryoVol   = QLineEdit("80, 100")
        self.replica   = QSpinBox()
        #self.divisions = QSpinBox()
        self.targetVol = QDoubleSpinBox()
        self.chooseLib = QComboBox()
        self.regionLib = QLineEdit()
        self.requestMessage = QTextEdit()

        ###Variables for [screenPlans]
        #self.crystname = QLineEdit()
        #self.divisions = QSpinBox()
        self.targetVol = QDoubleSpinBox()
        self.chooseLib = QComboBox()
        self.regionLib = QLineEdit()
        self.libraryInfo = []

        ###Variables for [cryoPlans]
        self.cryoStrategy = QComboBox()
        self.cryoSites  = QSpinBox()
        self.cryoVolume = QDoubleSpinBox()
        





        #[Final Result]
        self.targetPoints = {}
        ###Variables for [XtalViewer Configuration]
        #self.ImageProfile = "profileID_1"
        #self.ImageTypeDic = {'Visible':'1', 'Contrast':'3?', 'HighRes':'5', 'Polarize':'8'}


        ####Variavles for [???]
        self.iconColor    = '333333'




        leftPanel = QWidget()
        leftgrid = QGridLayout()
        leftgrid.addWidget(self.Configuration(), 0,0)
        leftgrid.addWidget(self.imgNavigator(),  1,0)
        leftgrid.addWidget(self.ObjectPlates(),  2,0)
        leftgrid.setAlignment(Qt.AlignLeft)
        leftPanel.setLayout(leftgrid)

        rightPanel = QWidget()
        rightgrid = QGridLayout()
        #rightgrid.addWidget(self.imgNavigator(),   0,0)
        rightgrid.addWidget(self.evaluePlans(),    0,0)
        rightgrid.addWidget(self.screenPlans(),    1,0)
        rightgrid.addWidget(self.cryoPlans(),      2,0)
        rightgrid.addWidget(self.requestBox(),     3,0)
        rightgrid.setAlignment(Qt.AlignTop)
        rightPanel.setLayout(rightgrid)

        grid = QGridLayout()
        grid.addWidget(leftPanel,           0,0)
        grid.addWidget(self.Viewer(),       0,1)
        grid.addWidget(rightPanel,          0,2)
        grid.setAlignment(Qt.AlignTop)
        self.setLayout(grid)






    def Configuration(self):
        groupbox = QGroupBox('Xtal Viewer Configure')
        grid = QGridLayout()

        ### Validate Given Configuration record
        screenConfig = '{0}/screen'.format(self.ConfigPath)


        if not os.path.isfile(screenConfig):
            self.screenConfig1 = self.screenConfig0
        try:
            self.screenConfig1 = misc.configToDict(screenConfig)
            xShift0 = int(self.screenConfig1['xShift'])
            yShift0 = int(self.screenConfig1['yShift'])
            imgScale0 = float(self.screenConfig1['imgScale'])
            self.guideShift1[0] = self.guideShift0[0] + xShift0
            self.guideShift1[1] = self.guideShift0[1] + yShift0
            print('[ViewerSetup] User-defined Configuration was found')
        except FileNotFoundError:
            self.screenConfig1 = self.screenConfig0
            print('[ViewerSetup] No Screen Configuration')
            print('[ViewerSetup] Viewer Setup as Default opt')
        except KeyError:
            self.screenConfig1 = self.screenConfig0
            print('[ViewerSetup] [!] Incomplete Configuration')
            print('[ViewerSetup] Viewer Setup as Default opt')
        except ValueError:
            self.screenConfig1 = self.screenConfig0
            print('[ViewerSetup] [!] Wrong Configuration')
            print('[ViewerSetup] Viewer Setup as Default opt')
        finally:
            print('[ViewerSetup] CircularGuideCenter : X-{0}px, Y-{1}px @ScalFactor=1'\
                  .format(self.guideShift1[0]+self.guideRadius,self.guideShift1[1]+self.guideRadius))
            print('[ViewerSetup] Image Magnification : {0}%'.format(self.screenConfig1['imgScale']))
        #self.ScreenSetting()

        ### Make GUI for Configuration
        imgTypes = sorted(self.ImageTypeDic.items(), key=lambda item:item[1])
        for key,val in imgTypes:
            self.ImageType.addItem(key)
        #self.ImageType.currentTextChanged.connect(self.selectImageType)
        imageprofile = self.ImageTypeDic[self.ImageType.currentText()]
        self.PlateType.addItem('SwissCI-MRC-3d')
        self.PlateType.addItem('SwissCI-MRC-2d')

        self.xCenter.setRange(-50,50)
        self.xCenter.move(1,1)
        self.xCenter.setTickPosition(QSlider.TicksBothSides)
        self.xCenter.setTickInterval(5)
        self.xCenter.setSingleStep(1)
        self.xCenter.setValue( int(self.screenConfig1['xShift']) )
        self.xCenter.sliderReleased.connect(self.moveCenterX)

        self.yCenter.setRange(-50,50)
        self.yCenter.move(1,1)
        self.yCenter.setTickPosition(QSlider.TicksBothSides)
        self.yCenter.setTickInterval(5)
        self.yCenter.setSingleStep(1)
        self.yCenter.setValue( int(self.screenConfig1['yShift']) )
        self.yCenter.sliderReleased.connect(self.moveCenterY)


        grid.addWidget(QLabel('ImageType'),      0,0,1,2)
        grid.addWidget(self.ImageType,           0,2,1,4)
        grid.addWidget(QLabel('PlateType'),      1,0,1,2)
        grid.addWidget(self.PlateType,           1,2,1,4)

        grid.addWidget(QLabel("Center X-axis"),    2,0,1,2)
        grid.addWidget(self.xCenter,               2,2,1,4)
        grid.addWidget(QLabel("Center Y-axis"),    3,0,1,2)
        grid.addWidget(self.yCenter,               3,2,1,4)
        grid.addWidget(self.restoreButton,         4,0,1,3)
        grid.addWidget(self.fixCurrButton,         4,3,1,3)        

        groupbox.setMaximumHeight(200)
        groupbox.setMaximumWidth(250)
        groupbox.setLayout(grid)
        return groupbox



    def resetScreenSetting(self):
        self.xCenter.setValue( int(self.screenConfig1['xShift']) )
        self.yCenter.setValue( int(self.screenConfig1['yShift']) )
        self.imgScale.setValue( float(self.screenConfig1['imgScale']) )
        self.loadImage()
        return 0

    def defaultScreenSetting(self):
        self.screenConfig1 = self.screenConfig0
        xShift = self.screenConfig1['xShift']
        yShift = self.screenConfig1['yShift']
        imgScale = self.screenConfig1['imgScale']
        self.guideShift1[0] = self.guideShift0[0] + xShift
        self.guideShift1[1] = self.guideShift0[1] + yShift
        self.resetScreenSetting()
        self.fixScreenSetting()
        return 0
    def fixScreenSetting(self):
        if not os.path.isdir(self.ConfigPath):
            misc.createFolder(self.ConfigPath)
        #print(self.window.sizeHint().width(), self.window.size().height())
        screenConfig = '{0}/screen'.format(self.ConfigPath)
        f = open(screenConfig, 'w')
        f.write( "xShift:{0}\n".format( str(self.xCenter.value()) ) )
        f.write( "yShift:{0}\n".format( str(self.yCenter.value()) ) )
        f.write( "imgScale:{0}\n".format( str(self.imgScale.value()) ) )
        f.close()
        print('[ViewerSetup] Current X,Y/ImgScale are Fixed', self.xCenter.value(),self.yCenter.value(), self.screenConfig1)
        return 0

    def selectImageType(self):
        return 0
    def targetSites(self):
        return 0
    def imageAdjustment(self):
        return 0

    def moveCenterX(self):
        print('centerX', self.guideShift1[0])
        print(self.xCenter.value())
        center = self.guideShift0[0] + self.xCenter.value()
        self.guideShift1[0] = center
        print('centerX', self.guideShift1[0])
        try:
            self.loadImage()
        except IndexError as e:
            pass

        return 0

    def moveCenterY(self):
        center = self.guideShift0[1] + self.yCenter.value()
        self.guideShift1[1] = center
        try:
            self.loadImage()
        except IndexError as e:
            pass
        return 0




    def ObjectPlates(self):
        groupbox = QGroupBox('Object Plates')
        grid = QGridLayout()

        enterload = QPushButton('Load', self)
        enterload.clicked.connect(self.LoadPlates)
        self.objectPlates.returnPressed.connect(self.LoadPlates)
        self.tableview.setModel(self.tableview_model)
        self.tableview.setSelectionBehavior(QAbstractItemView.SelectRows)
        #Plate list DoubleClick Event
        self.tableview.doubleClicked.connect( self.setCurrentPlate )

        delete = QPushButton('Delete')
        delete.clicked.connect( self.deletePlate )
        refresh = QPushButton('Refresh')
        refresh.clicked.connect( self.refreshPlates )
        clear = QPushButton('Clear')
        clear.clicked.connect( self.clearPlates )

        grid.addWidget(QLabel('Protein'),   0,0,1,2)
        grid.addWidget(self.crystname,      0,2,1,4)
        grid.addWidget(QLabel('Plates ID'), 1,0,1,2)
        grid.addWidget(self.objectPlates,   1,2,1,3)
        grid.addWidget(enterload,           1,5,1,1)
        grid.addWidget(self.tableview,      2,0,1,6)
        grid.addWidget(refresh,             3,0,1,2)
        grid.addWidget(delete,              3,2,1,2)
        grid.addWidget(clear,               3,4,1,2)

        groupbox.setMaximumWidth(250)
        groupbox.setLayout(grid)
        return groupbox

    def LoadPlates(self):
        rawList = []
        rawCatalog = {}
        wrongPlates = []
        imageProfile = self.ImageTypeDic[self.ImageType.currentText()]
        if self.objectPlates.text():
            #Concerning
            rawList = self.objectPlates.text().split(',')
            for item in rawList:
                pID = item.strip()
                self.plateCatalogue[pID] = func.checkPlates(pID, imageProfile, self.targetPoints, self.pathway['rmserver'],  self.pathway['cellar'])
            plateCatalogue, wrongPlates = func.CatalogueThinning(self.plateCatalogue)
            self.plateCatalogue = plateCatalogue
            self.refreshPlates()

            if len(wrongPlates) > 0:
                react = QMessageBox.warning(self, "Check plateID", '\n'.join(wrongPlates), QMessageBox.Yes)
                print('[ Warning ] Invalid Plates')
                if react == QMessageBox.Yes:
                    print('ok')
                else: pass
            else: pass
        else:
            pass
        return 0
    def clearPlates(self):
        self.tableview_model.removeRows(0, self.tableview_model.rowCount())
        self.tableview_model.removeColumns(0, self.tableview_model.columnCount())
        self.plateCatalogue.clear()
        self.targetPoints.clear()
        return 0

    def refreshPlates(self):
        self.tableview_model.removeRows(0, self.tableview_model.rowCount())
        self.tableview_model.removeColumns(0, self.tableview_model.columnCount())
        i = 0
        for plate, state in sorted(self.plateCatalogue.items()):
           cellwidget = QWidget()
           layout = QHBoxLayout(cellwidget)
           layout.setAlignment(Qt.AlignCenter)
           layout.setContentsMargins(0, 0, 0, 0)
           cellwidget.setLayout(layout)
           state['selected'] = len( [ key for key,val in self.targetPoints.items() if key.startswith(plate) ] )
           #column00 = QStandardItem()
           column01 = QStandardItem(plate)
           column02 = QStandardItem(str(state['drops']))
           column03 = QStandardItem(str(state['selected']))
           self.tableview_model.setHorizontalHeaderLabels(['plateID', 'Drops', 'Select'])
           #self.tableview.setIndexWidget(self.tableview_model.index(i, 0), cellwidget)
           self.tableview_model.setItem(i, 0, column01)
           self.tableview_model.setItem(i, 1, column02)
           self.tableview_model.setItem(i, 2, column03)
           self.tableview.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
           self.tableview.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
           self.tableview.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
           column01.setTextAlignment(Qt.AlignHCenter|Qt.AlignVCenter|Qt.AlignCenter)
           column02.setTextAlignment(Qt.AlignHCenter|Qt.AlignVCenter|Qt.AlignCenter)
           column03.setTextAlignment(Qt.AlignHCenter|Qt.AlignVCenter|Qt.AlignCenter)
           self.tableview.setIndexWidget(self.tableview_model.index(i, 0), cellwidget)
           i += 1
        self.tableview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        #self.setCurrentPlate()
        print(self.plateCatalogue)
        #print(self.currPlate_info)
        """
        try:
            imgPath = self.currPlate_info[self.currWellNo][-1]
            print('[CurrentImg] ImgPath: {}'.format(imgPath))
            print('[Image Load] Original Image : {0} x {1}'.format( self.OrgImage[0], self.OrgImage[1] ))
            #print(self.currPlate_info[self.currWellNo])
            # ['1', '700', 'A01a', 'wellNum_1', '1', '2021-09-11 23:14:33.976878', \
            #  '/smbmount/rmserver/RockMakerStorage/WellImages/700/plateID_700/batchID_3553/wellNum_1/profileID_1/d1_r70821_ef.jpg']
            print('[Image Load] ScaleFactor = {0}'.format( self.imgScale.value()/100 ) )
            print('[Image Load] Resize to {0} x {1}'.format(self.ImgWidth1, self.ImgHeight1))
        except IndexError as e:
            print('[Load Plate] Any plate did not selected')
        """
        return 0

    def deletePlate(self):
        #remove selected item from tableview
        #remove selected item from self.plateCatalogue(?)
        #Indirect way :(
        selectedRow = self.tableview.currentIndex().row()
        selectedPID = self.tableview_model.item(selectedRow, 0).text()
        del(self.plateCatalogue[selectedPID])
        print(self.targetPoints)
        for item in [key for key in self.targetPoints.keys() if key.startswith(selectedPID)]:
            del(self.targetPoints[item])
        print(self.targetPoints)
        
        self.refreshPlates()
        return 0

    def setCurrentPlate(self):
        row = self.tableview.currentIndex().row()
        print('row:',row)
        self.currentPlate.setText( self.tableview_model.item(row, 0).text() )
        #self.currPlate_view.setText(self.currentPlate
        currPlate_infoPath = "{0}/{2}_pID{1}_info.csv".format(self.pathway['cellar'], self.currentPlate.text(), misc.getUsername())
        self.currPlate_info = misc.csvToList(currPlate_infoPath)
        self.currWellNo = 1
        self.setCurrentWell()
        return 0
    def setCurrentWell(self):
        try:
            #self.currPlate_info[self.currWellNo][-1] = \
            #['3', '690', 'A01d', 'wellNum_1', '3', '2021-09-10 15:53:35.746319', '.../690/plateID_690/batchID_3552/wellNum_1/profileID_1/d3_r69383_ef.jpg']
            #[nu, plateID, Well, WellNo, Subwell, ImgCreaged, IMGpath]
            self.currentWell.setText( self.currPlate_info[self.currWellNo][2] )
            self.loadImage()
            #print('[LoadImage] Current Well : {}'.format(self.currPlate_info[self.currWellNo]))
            print('[CurrentWell] pID{0} {1}'.format(self.currentPlate.text(), self.currPlate_info[self.currWellNo][2]))
            #It will be used for restore to right well when user give wrong well to self.currentWell
            self.lastWell = self.currPlate_info[self.currWellNo][2]
        except IndexError: pass
        #selectedWellInThisPlate = len( [ key for key,val in self.targetPoints.items() if key.startswith(self.currentPlate) ] )
        
        #self.currSelect_view.setText(str(targetsInThisPlate))
        #print(targetsInThisPlate)


    def zero(self):
        return 0


    def Viewer(self):
        Viewer = QWidget()
        self.pathLineEdit = QLineEdit()
        self.imageLabel = QLabel()
        self.imageLabel.setPixmap(QPixmap())
        self.imageLabel.mousePressEvent = self.imageClickEvent

        self.OrgImgage = [0,0]
        self.ImgWidth0 = 918
        self.ImgHeight0 = 768
        self.ImgWidth1 = self.ImgWidth0
        self.ImgHeight1 = self.ImgHeight0
        self.dImgWidth = 0
        self.dImgHeight = 0
        self.imageLabel.setFixedWidth(self.ImgWidth0)
        self.imageLabel.setFixedHeight(self.ImgHeight0)

        self.pixmap = QPixmap()
        self.pixmap2 = QPixmap()

        grid = QGridLayout()
        #grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        grid.addWidget(self.imageLabel, 0,0)
        Viewer.setLayout(grid)
        return Viewer

    def loadImage(self):
        imgPath = self.currPlate_info[self.currWellNo][-1]
        imgKey  = '{0}_{1}'.format(self.currPlate_info[self.currWellNo][1], self.currPlate_info[self.currWellNo][2])
        #Fix value
        self.imgScale.setValue(75)
        if imgPath != 'yet':
            #print('[LoadImage]imgPath: {}'.format(imgPath))
            image = QImage(imgPath)
            if image.isNull():
                QMessageBox.information(self, "Image Viewer", "Cannot load %s." % imgPath)
                return
            self.pathLineEdit.setText(imgPath)

            #print('[LoadImage] Original Image : {0} x {1}'.format( image.width(), image.height() ))
            self.OrgImage[0] = image.width()
            self.OrgImage[1] = image.height()
            #print('orgimage',self.OrgImage[0])
            self.ImgWidth1 = self.OrgImage[0] * self.imgScale.value()/100
            self.ImgHeight1 = self.OrgImage[1] * self.imgScale.value()/100
            #print(self.imgScale.value())
            #self.imageLabel.setFixedWidth(self.ImgWidth1)
            #self.imageLabel.setFixedHeight(self.ImgHeight1)
            #print('[Image Load] ScaleFactor = {0}'.format( self.imgScale.value()/100 ) )
            #print('[Image Load] Resize to {0} x {1}'.format(self.ImgWidth1, self.ImgHeight1))

            self.pixmap = QPixmap(image)

            pen = QPen(QColor('#ffffff'), 5)
            pen2 = QPen(QColor('#ff0000'), 5)
            blue1   = QPen(QColor('#01567f'), 10)
            blue2   = QPen(QColor('#4cc4ff'),  3)
            violet1 = QPen(QColor('#744080'), 10)
            violet2 = QPen(QColor('#f5cdff'), 3)
            green1  = QPen(QColor('#017f7b'), 10)
            green2  = QPen(QColor('#4cfff9'), 3)
            painter = QPainter(self.pixmap)
            painter.setPen(pen)
            radius = self.guideRadius
            rowXoffset = int( self.currentWell.text()[1:3] ) * self.colOffset[0] - self.colOffset[0]
            rowYoffset = int( self.currentWell.text()[1:3] ) * self.colOffset[1] - self.colOffset[1]
            colXoffset = ( ord(self.currentWell.text()[0]) - 65 ) * self.rowOffset[0]
            colYoffset = ( ord(self.currentWell.text()[0]) - 65 ) * self.rowOffset[1]
            Xoffset = rowXoffset + colXoffset
            Yoffset = rowYoffset + colYoffset

            center = [ self.guideShift1[0]+radius+Xoffset,  self.guideShift1[1]+radius+Yoffset ]
            cross = 30
            #r = QRectF(self.guideShift1[0], self.guideShift1[1], radius*2, radius*2)
            r = QRectF(center[0]-radius, center[1]-radius, radius*2, radius*2)
            painter.drawEllipse(r)
            painter.drawLine( center[0]-cross/2, center[1], center[0]+cross/2, center[1])
            painter.drawLine( center[0] ,center[1]-cross/2, center[0], center[1]+cross/2)
            painter.setPen(pen2)
            painter.drawPoint(center[0],center[1])


            try:
                for points in self.targetPoints[imgKey]:
                    #orgX = center[0]+points[3]/(self.imgScale.value()/100)
                    #orgY = center[1]+points[4]/(self.imgScale.value()/100)
                    orgX = center[0]+points[3]
                    orgY = center[1]+points[4]
                    #30px = 5nl, 45px = 10nl
                    radi = 30*2 /(self.imgScale.value()/100)
                    if points[0].startswith('chem'):
                        painter.setPen(blue1)
                        painter.drawPoint( orgX, orgY )
                        painter.setPen(blue2)
                        painter.drawEllipse( QRectF(orgX-radi/2, orgY-radi/2, radi, radi) )
                    elif points[0].startswith('cryo'):
                        painter.setPen(violet1)
                        painter.drawPoint( orgX, orgY )
                        painter.setPen(violet2)
                        painter.drawEllipse( QRectF(orgX-radi/2, orgY-radi/2, radi, radi) )
                    else:
                        painter.setPen(green1)
                        painter.drawPoint( orgX, orgY )
                        painter.setPen(green2)
                        painter.drawEllipse( QRectF(orgX-radi/2, orgY-radi/2, radi, radi) )
            except:
                pass
            painter.end()

            self.pixmap = self.pixmap.scaledToWidth(self.ImgWidth1)
            self.pixmap = self.pixmap.scaledToHeight(self.ImgHeight1)
            self.imageLabel.setPixmap(self.pixmap)
        else: pass


    def setTarget(self):
        groupbox = QGroupBox()
        #widget = QWidget()
        grid = QGridLayout()

        self.divisions.setValue(2)
        self.targetVol.setSingleStep(2.5)
        self.targetVol.setDecimals(1)
        self.targetVol.setSuffix('nl')

        grid.addWidget(QLabel("Targets/Well"), 0,0)
        grid.addWidget(self.divisions,        0,1)
        grid.addWidget(QLabel("TargetVolume"), 1,0)
        grid.addWidget(self.targetVol,         1,1)
        
        #groupbox.setMaximumHeight(250)
        #groupbox.setMaximumWidth(250)
        groupbox.setLayout(grid)

        return groupbox

        return 0

    def imgNavigator(self):
        groupbox = QGroupBox()
        widget = QWidget()
        grid = QGridLayout()


        self.currentWell.returnPressed.connect(self.moveToWell)
        self.currentPlate.returnPressed.connect(self.moveToPlate)

        buttonWidget = QWidget()
        buttonbox = QHBoxLayout()
        homeButton = QPushButton()
        prevButton = QPushButton()
        nextButton = QPushButton()
        lastButton = QPushButton()
        #undoButton = QPushButton()
        #self.undoButton.setCheckable(True)

        homeButton.clicked.connect(self.moveFirstWell)
        prevButton.clicked.connect(self.movePrevWell)
        nextButton.clicked.connect(self.moveNextWell)
        lastButton.clicked.connect(self.moveLastWell)
        self.undoButton.clicked.connect(self.removePoint)
        #undoButton.clicked.connect(self.removeLastTarget)

        homeButton.setIcon(QIcon('{0}/{1}_first.png'.format(self.accpath['icons'],self.iconColor)))
        prevButton.setIcon(QIcon('{0}/{1}_prev.png'.format(self.accpath['icons'],self.iconColor)))
        nextButton.setIcon(QIcon('{0}/{1}_next.png'.format(self.accpath['icons'],self.iconColor)))
        lastButton.setIcon(QIcon('{0}/{1}_end.png'.format(self.accpath['icons'],self.iconColor)))
        self.undoButton.setIcon(QIcon('{0}/{1}_eraser.png'.format(self.accpath['icons'],self.iconColor)))
        #tapeButton.setIcon(QIcon('{0}/{1}_ruler.png'.format(self.accpath['icons'],self.iconColor)))

        buttonbox.addWidget(homeButton)
        buttonbox.addWidget(prevButton)
        buttonbox.addWidget(nextButton)
        buttonbox.addWidget(lastButton)
        buttonbox.addWidget(self.undoButton)
        buttonWidget.setLayout(buttonbox)
        buttonWidget.setMinimumHeight(37)
        saveButton = QPushButton("Targets in Current plate")
        saveButton.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogSaveButton')))
        saveButton.clicked.connect(self.saveFreeTarget)

        grid.addWidget(QLabel("Current Plate"),  0,0,1,2)
        grid.addWidget(self.currentPlate,        0,2,1,2)
        grid.addWidget(QLabel("Current Well"),   1,0,1,2)
        grid.addWidget(self.currentWell,         1,2,1,2)
        grid.addWidget(QLabel("Target/Well"),    2,0,1,2)
        grid.addWidget(self.divisions,           2,2,1,2)
        #grid.addWidget(QLabel("Selected Well"),  3,1,1,2)
        #grid.addWidget(self.currSelect_view,     3,3,1,1)
        grid.addWidget(buttonWidget,             3,0,1,4)
        #grid.addWidget(saveButton,               4,0,1,2)
        grid.setColumnStretch(0,3)
        grid.setColumnStretch(1,0)
        grid.setColumnStretch(2,0)

        #PyQt5.QtCore.QSize(800, 669)
        #groupbox.setMinimumWidth(self.OrgImage[0]*self.imgScale.value()/100)
        #groupbox.setMaximumWidth(self.OrgImgWidth*self.imgScale.value()/100)
        #groupbox.setMinimumHeight(220)
        groupbox.setMaximumHeight(250)
        groupbox.setMaximumWidth(250)
        groupbox.setLayout(grid)

        return groupbox

    def moveToPlate(self):
        print('movemove')
        wannaGo = str( self.currentPlate.text().strip() )
        plates = [ str(key) for key,val in self.plateCatalogue.items() ]
        if wannaGo in plates:
            currPlate_infoPath = "{0}/{2}_pID{1}_info.csv".format(self.pathway['cellar'], wannaGo, misc.getUsername())
            self.currPlate_info = misc.csvToList(currPlate_infoPath)
            print('[Load Plate] pID{0} selected'.format(wannaGo))
            #self.currentWell = 1
            self.currWellNo = 1
            #self.currPlate_view.setText(self.currentPlate)
            self.setCurrentWell()
        else:
            message = "pID{0} is not Loaded".format( self.currWell_view.text() )
            react = QMessageBox.warning(self, "Check Plate ID", message , QMessageBox.Yes)
            if react == QMessageBox.Yes:
                pass


    def moveToWell(self):
        switch = 0
        for i,item in enumerate(self.currPlate_info):
            #print(i, item[2])
            if item[2] == str( self.currentWell.text().strip() ):
                self.currWellNo = i
                switch = 1
                self.setCurrentWell()
            else: pass
        if switch == 0:
            message = "{1} not found in pID{0}".format( self.currentPlate.text(), self.currentWell.text() )
            react = QMessageBox.warning(self, "Check Well ID", message , QMessageBox.Yes)
            if react == QMessageBox.Yes:
                #Set self.currentWell.text() as previous one
                self.currentWell.setText(self.lastWell)
        return 0
    def movePrevWell(self):
        pID = self.currentPlate.text()
        if pID != 'none' or pID != '':
            if self.currWellNo == 1:
                print('[moveToWell] First Well in This Plate')
            else:
                self.currWellNo += -1
                self.setCurrentWell()
                #print('[moveToWell] {0} >> {1}'.format( self.currPlate_info[self.currWellNo+1][2], self.currPlate_info[self.currWellNo][2] ))   

        else: pass
        return 0
    def moveNextWell(self):
        pID = self.currentPlate.text()
        lastWellNo = len( self.currPlate_info ) - 1
        if pID != 'none' or pID != '':
            if self.currWellNo == lastWellNo:
                print('[MoveToWell] Last Well in This Plate')
            else:
                self.currWellNo += 1
                self.setCurrentWell()
                #print('[moveToWell] {0} >> {1}'.format( self.currPlate_info[self.currWellNo-1][2], self.currPlate_info[self.currWellNo][2] ))
        else: pass
        return 0

    def moveFirstWell(self):
        pID = self.currentPlate.text()
        if pID != 'none' or pID != '':
            if self.currentWell == 1:
                pass
            else:
                self.currWellNo = 1
                self.setCurrentWell()
        else: pass
        return 0
    def moveLastWell(self):
        pID = self.currentPlate.text()
        lastWellNo = len( self.currPlate_info ) - 1
        if pID != 'none' or pID != '':
            if self.currentWell == lastWellNo:
                pass
            else:
                self.currWellNo = lastWellNo
                self.setCurrentWell()
        else: pass
        return 0
        return 0

    def removeLastTarget(self):
        return 0

    def removePoint(self):
        #1. Eraser Flag = 1
        #2. left click
        #3. remove point from self.targetPoints
        #4. self.loadImage
        #5. Eraser Flag = 0
        #self.eraserFlag = True
        if self.eraserFlag == False: self.eraserFlag = True
        elif self.eraserFlag == True : self.eraserFlag = False
        #self.distanToPoint(0,0)
        return 0


    def distanToPoint(self,x_px,y_px):
        pID = self.currentPlate.text()
        well = self.currentWell.text()
        imgKey = '{0}_{1}'.format(pID,well)
        radi = 30 /(self.imgScale.value()/100) #Pixcel
        toRemove = False
        try:
            for i, item in enumerate(self.targetPoints[imgKey]):
                dist_px = math.sqrt( (x_px - item[-2])**2+(y_px - item[-1])**2 )
                print(i, item,dist_px, dist_px/self.guideRadius*self.wellRadius)
                if dist_px <= radi:
                    toRemove = i
                print(i, item, toRemove)
            del self.targetPoints[imgKey][toRemove]
            print('remove')
            self.loadImage()
        except KeyError as e:
            #imgKey is not found in self.targetPoints
            print('[Eraser] imgKey:{0} is not selected'.format(imgKey))
            self.eraserFlag = False
        except IndexError as e:
            #self.targetPoints[imgKey] is vacant
            print('[Eraser] Any Point is not found')
            self.eraserFlag = False
        return 0

    def saveFreeTarget(self):
        return 0


    def setChemicals(self):
        groupbox = QGroupBox("Fragment Library")
        #widget = QWidget()
        grid = QGridLayout()

        grid.addWidget(QLabel("Targets/Well"), 0,0)
        grid.addWidget(self.divisions,        0,1)
        grid.addWidget(QLabel("TargetVolume"), 1,0)
        grid.addWidget(self.targetVol,         1,1)

        #groupbox.setMaximumHeight(250)
        #groupbox.setMaximumWidth(250)
        groupbox.setLayout(grid)

        return groupbox


    def requestBox(self):
        groupbox = QGroupBox("RequestMessage")
        grid = QGridLayout()
        #test = QTextEdit()
        btnClear = QPushButton("clear")
        btnClear.clicked.connect(self.clearRequest)
        grid.addWidget(self.requestMessage,    0,0,10,15)
        grid.addWidget(btnClear,               10,0,1,15)
        groupbox.setLayout(grid)

        return groupbox
    def clearRequest(self):
        self.requestMessage.clear()
        return 0
    def evaluePlans(self):
        groupbox = QGroupBox("Evaluate Plan")
        widget = QWidget()
        grid = QGridLayout()

        #libList = ['**none**']
        #for item in os.listdir(self.pathway['library']):
        #    print('[Libraries]', item)
        #    if item.endswith('.csv'):
        #        #filename = '{0}/{1}'.format(self.pathway['library'], item)
        #        libname  = item.replace('.csv', '')
        #        libList.append(libname)
        #    else: pass
        #for item in sorted(libList):
        #    self.chooseLib.addItem(item)
        #self.chooseLib.currentTextChanged.connect( self.zero )


        #self.btnCheckEvalueMethod = QPushButton("Method Duration")
        self.btnCheckEvalueMethod.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_MessageBoxInformation')))
        self.btnCheckEvalueMethod.clicked.connect(self.giveUserMessage)
        btnSaveTargets = QPushButton("Targets Only")
        btnSaveTargets.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogSaveButton')))
        btnSaveTargets.clicked.connect(self.saveTargetList)
        btnSaveWorksheet = QPushButton("Worksheets")
        btnSaveWorksheet.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogSaveButton')))
        btnSaveWorksheet.clicked.connect(self.makeEvalWorksheet)
        self.btnEvalueToMxlive = QPushButton("Upload To MxLive")
        self.btnEvalueToMxlive.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_ArrowUp')))
        self.btnEvalueToMxlive.clicked.connect(self.deliverToMxlive)

        #self.divisions.setValue(2)
        self.divisions.setRange(1,10)
        self.replica.setRange(1,100)
        self.targetVol.setRange(2.5, 300)
        self.targetVol.setSingleStep(2.5)
        self.targetVol.setDecimals(1)
        self.targetVol.setSuffix('nl')
        #self.chooseLib.setCurrentIndex(libList.index('none'))
        self.chooseLib.setCurrentIndex(0)
        #self.chooseLib.currentIndexChanged.connect(self.selectLibrary)
        self.replica.setValue(3)       
 
        #grid.addWidget(QLabel("Protein"),         0,0,1,4)
        #grid.addWidget(self.crystname,            0,4,1,6)
        grid.addWidget(QLabel("SolventName"),     1,0,1,4)
        grid.addWidget(self.solvents,             1,4,1,6)
        grid.addWidget(QLabel("Incubation"),      2,0,1,4)
        grid.addWidget(self.incuTime,             2,4,1,6)
        grid.addWidget(QLabel("Solvent (nl)"),    3,0,1,4)
        grid.addWidget(self.solventVol,           3,4,1,6)
        grid.addWidget(QLabel("CryoSol (nl)"),    4,0,1,4)
        grid.addWidget(self.cryoVol,              4,4,1,6)
        grid.addWidget(QLabel("Replication"),     5,0,1,4)
        grid.addWidget(self.replica,              5,4,1,6)
        #grid.addWidget(QLabel("Target/Well"),     6,0,1,4)
        #grid.addWidget(self.divisions,            6,4,1,6)
        grid.addWidget(self.btnCheckEvalueMethod,            6,0,1,10)
        grid.addWidget(btnSaveTargets,            7,0,1,5)
        grid.addWidget(btnSaveWorksheet,          7,5,1,5)        
        grid.addWidget(self.btnEvalueToMxlive,         8,0,1,10)
 
        groupbox.setLayout(grid)

        return groupbox


    def screenPlans(self):
        groupbox = QGroupBox("Screen Plan")
        widget = QWidget()
        grid = QGridLayout()

        libList = ['**none**']
        for item in os.listdir(self.pathway['library']):
            print('[Libraries]', item)
            if item.endswith('.csv'):
                #filename = '{0}/{1}'.format(self.pathway['library'], item)
                libname  = item.replace('.csv', '')
                libList.append(libname)
            else: pass
        for item in sorted(libList):
            self.chooseLib.addItem(item)
        self.chooseLib.currentTextChanged.connect( self.zero )

        btnSaveTargets = QPushButton("Targets Only")
        btnSaveTargets.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogSaveButton')))
        btnSaveTargets.clicked.connect(self.saveTargetList)
        btnSaveWorksheet = QPushButton("Worksheets")
        btnSaveWorksheet.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogSaveButton')))
        btnSaveWorksheet.clicked.connect(self.saveChemWorksheet)
        self.btnScreenToMxlive = QPushButton("Upload To MxLive")
        self.btnScreenToMxlive.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_ArrowUp')))
        self.btnScreenToMxlive.clicked.connect(self.deliverToMxlive)

        #self.divisions.setValue(2)
        self.divisions.setRange(1,10)
        self.targetVol.setRange(2.5, 300)
        self.targetVol.setSingleStep(2.5)
        self.targetVol.setDecimals(1)
        self.targetVol.setSuffix('nl')
        #self.chooseLib.setCurrentIndex(libList.index('none'))
        self.chooseLib.setCurrentIndex(0)
        self.chooseLib.currentIndexChanged.connect(self.selectLibrary)

        #grid.addWidget(QLabel("Protein"),         0,0,1,4)
        #grid.addWidget(self.crystname,            0,4,1,6)
        #grid.addWidget(QLabel("Target/Well"),     1,0,1,4)
        #grid.addWidget(self.divisions,            1,4,1,6)
        grid.addWidget(QLabel("Library"),         2,0,1,4)
        grid.addWidget(self.chooseLib,            2,4,1,6)
        grid.addWidget(QLabel("Region"),          3,0,1,4)
        grid.addWidget(self.regionLib,            3,4,1,6)
        grid.addWidget(QLabel("Vol/Well"),        4,0,1,4)
        grid.addWidget(self.targetVol,            4,4,1,6)

        grid.addWidget(btnSaveTargets,            5,0,1,5)
        grid.addWidget(btnSaveWorksheet,          5,5,1,5)
        grid.addWidget(self.btnScreenToMxlive,         6,0,1,10)

        groupbox.setLayout(grid)

        return groupbox




    def cryoPlans(self):
        groupbox = QGroupBox("Cryo Plan")
        grid = QGridLayout()


        sites = ['To Selected Points', 'To 4 Sides', 'To 6 Sides', 'Assign Manually (yet)']
        for item in sites:
            self.cryoStrategy.addItem(item)
        self.cryoSites.setRange(0,10)
        self.cryoVolume.setRange(0, 1000)
        self.cryoVolume.setSingleStep(0)
        self.cryoVolume.setDecimals(1)
        self.cryoVolume.setSuffix('nl')

        btnStartManualSelect = QPushButton("Start To Targeting")
        btnClearCryoPoints   = QPushButton("Cancel Targeting")
        btnSaveCryoWorksheet = QPushButton("Save")
        btnSendCryoWorksheet = QPushButton("SendToECHO")
        btnSaveCryoWorksheet.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogSaveButton')))
        btnSendCryoWorksheet.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_ArrowRight')))
        self.btnCheckCryoMethod.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_MessageBoxInformation')))
        self.btnCheckCryoMethod.clicked.connect(self.giveUserMessage)

        self.cryoStrategy.currentIndexChanged.connect(self.selectCryoPlans)

        grid.addWidget(QLabel("Transfer"),        0,0,1,4)
        grid.addWidget(self.cryoStrategy,         0,4,1,6) 
        grid.addWidget(QLabel("Volume/Well"),     1,0,1,4)
        grid.addWidget(self.cryoVolume,           1,4,1,6)
        grid.addWidget(QLabel("Source Well"),     2,0,1,4)
        grid.addWidget(QLineEdit(),               2,4,1,6)
        #grid.addWidget(QLabel("Targets/Well"),    3,0,1,4)
        #grid.addWidget(self.cryoSites,            3,4,1,6)
        grid.addWidget(self.btnCheckCryoMethod,   3,0,1,10)
        #grid.addWidget(btnStartManualSelect,      4,0,1,10)
        #grid.addWidget(btnClearCryoPoints,        5,0,1,10)
        grid.addWidget(btnSaveCryoWorksheet,      6,0,1,5)
        grid.addWidget(btnSendCryoWorksheet,      6,5,1,5)
        groupbox.setLayout(grid)

        return groupbox

    def selectCryoPlans(self):
        strategy = self.cryoStrategy.currentText()
        ### Final Result Form (Constant for this DeF)
        echoHeader = ['Type', \
                      'Source plate name', 'Source well', 'Transfer Volume', \
                      'Destination plate name', 'Targeting Well(forView)','Destination Well',\
                      'Destination Well X offset', 'Destination Well Y offset']
        echoCryoList = [echoHeader]
        cryoVolume = self.cryoVolume.value()
        plateType = self.PlateType.currentText()
        srcPlate = 'SourcePlate'
        srcType  = 'CryoProtectant'
        srcWell  = 'A00'
        #self.targetPoints >> echoCryoList
        if strategy == 'To Selected Points':
            print('yet')
            for key, val in self.targetPoints.items():
                ctimes = float(cryoVolume) / 2.5 # 2.5ul : Minimum transfer value
                cquota = ctimes // len(val)
                cremain = ctimes % len(val)
                destPlate = key.split('_')[0]
                destWell1 = key.split('_')[1]
                destWell2 = adpt.transPlate(plateType, destWell1)
                for i, item in enumerate(val):
                    if i+1 == len(val):
                        ctransVol = (cquota+cremain) * 2.5
                    else:
                        ctransVol = cquota * 2.5
                    destYoffset = item[2]
                    if destWell1.endswith('d'): destXoffset = item[1] -700
                    else                      : destXoffset = item[1]
                    echoCryoList.append( ['CryoProtectant', srcPlate, srcWell, ctransVol, \
                                           destPlate, destWell1, destWell2, destXoffset, destYoffset] )

            for item in echoCryoList:
                print('CryoTEST', item) 
        elif strategy == 'To 4 Sides':
            print('yet')
        elif strategy == 'To 6 Sides':
            print('yet')
        elif strategy == 'Assign Manually':
            print('yet')
            print(self.targetPoints)
            selectedImgKeys = []
            for key,val in self.targetPoints.items():
                selectedImgKeys.append(key)
            print('selected', selectedImgKeys)
            firstPlate = selectedImgKeys[0].split('_')[0]
            firstWell  = selectedImgKeys[0].split('_')[1]
            print(firstPlate, firstWell)
            self.currentPlate.setText(firstPlate)
            self.moveToPlate()
            self.currentWell.setText(firstWell)
            self.moveToWell()
           
        for item in echoCryoList:
            print('CryoTEST', item)

        else: pass
        return 0
    def takeCryoPoints(self):
        #Flag of the cryo targeting
        #Move to First Selected Well

        return 0


    def selectLibrary(self):
        ### Import User Input : from QLineEdit to List
        library = self.chooseLib.currentText()
        
        libfile = "{0}/{1}.csv".format(self.pathway['library'],library)
        #Reset the objective Library information 
        self.libraryInfo.clear()
        with open(libfile, 'r') as f:
            contents = csv.reader(f)
            print(type(contents))
            for line in contents:
                self.libraryInfo.append(line)

        #Analyse header
        #header = ['Vendor', 'Library', 'No', 'ID', 'Formula', 'MW', 'Smile', 'Conc_mM', 'Solvent', 'Plate_ID', 'Plate_well']
         
        first = 1
        last = len(self.libraryInfo) -1
        self.regionLib.setText('{0}-{1}'.format(first, last))
        return 0

    def getRegion(self):
        region0 = self.regionLib.text()
        regions = region0.replace(' ','').split(',')
        chosenWell = []
        newRegions = []
        first = 1
        last  = len(self.libraryInfo)-1
        for i,item in enumerate(regions):
            try:
                if '-' in item:
                    start = int(item.replace(' ', '').split('-')[0])
                    end   = int(item.replace(' ', '').split('-')[1])
                    if start < first : start = first
                    if end > last    : end = last
                    newitem = '-'.join([str(start),str(end)])
                    for no in range(start,end+1, 1):
                        chosenWell.append(no)
                elif '~' in item:
                    start = int(item.replace(' ', '').split('~')[0])
                    end   = int(item.replace(' ', '').split('~')[1])
                    if start < first : start = first
                    if end > last    : end = last
                    newitem = '-'.join([str(start),str(end)])
                    for no in range(start,end+1, 1):
                        chosenWell.append(no)
                else:
                    if int(item) >= first and int(item) <= last:
                        chosenWell.append(int(item))
                        newitem = item
                    else: 
                        newitem = False
                if newitem:
                    newRegions.append(newitem)
            except ValueError as e: pass
        self.regionLib.setText(', '.join(newRegions))
        return chosenWell

    def moveNextTarget(self):
        return 0
    def saveTargetList(self):
        defaultname = "target.csv"
        filesave = QFileDialog.getSaveFileName(self,"Save free target (csv)",\
                                               #self.pathway['userhome']+'/Documents/'+defaultname, "CSV Files (*.csv)")
                                               USERHOME_PATH +'/Documents/'+defaultname, "CSV Files (*.csv)")
        if filesave[0] != '':
            if filesave[0].endswith('.csv'):
                filename = filesave[0]
            else:
                filename = filesave[0] + '.csv'
            targetList = self.makeTargetList() 
            with open(filename, 'w') as f:
                csvWriter = csv.writer(f)
                for line in targetList:
                    csvWriter.writerow(line)
            
        return 0
    def makeTargetList(self):
        #print(self.targetPoints)
        header = ['Destination plate name', 'Targeting Well(forView)','Destination Well',\
                  'Destination Well X offset', 'Destination Well Y offset']
        plateType = self.PlateType.currentText()
        targetList = [header]
        for key,val in self.targetPoints.items():
            #print(key, val)
            for item in val:
                destPlate = key.split('_')[0]
                destWell1 = key.split('_')[1]
                destWell2 = adpt.transPlate(plateType,'384', destWell1) 
                destYoffset = item[2]
                if destWell1.endswith('d'): destXoffset = item[1] -700
                else                      : destXoffset = item[1]
                targetList.append( [destPlate, destWell1, destWell2, destXoffset, destYoffset] )
        for item in targetList:
            print('[TargetOnly]',item)
        return targetList



    def scanEvalMethod(self):
        ### Import User Input : from QLineEdit to List
        solvents = self.solvents.text().replace(' ', '').split(',')
        times    = self.incuTime.text().replace(' ', '').split(',')
        solvVol  = self.solventVol.text().replace(' ', '').split(',')
        cryoVol  = self.cryoVol.text().replace(' ', '').split(',')
        replica  = self.replica.value()
        #if solvents == ['']: solvents = []
        #if times    == ['']: times    = []
        #if solvVol  == ['']: solvVol  = []
        #if cryoVol  == ['']: cryoVol  = []
        ### Remove after stablized
        print('[EvaluateWorksheet] {0} Solvents:{1}'.format(len(solvents), solvents))
        print('[EvaluateWorksheet] {0} time harvest:{1}'.format(len(times), times))
        print('[EvaluateWorksheet] {0} Solvent Volumes:{1} nl'.format(len(solvVol), solvVol))
        print('[EvaluateWorksheet] {0} Cryopro Volumes:{1} nl'.format(len(cryoVol),cryoVol))

        ### Variable Declaration
        appendixes = []
        doSolvent = [] #for echo worksheet [harvest time, solvent type, total volume to be added in a drop]
        doCryopro = [] #for echo worksheet [harvest time, 'CRYO'      , total volume to be added in a drop]
        durationVolume = {}            #Variable for the required volume for each solutions
        for item in solvents+['CRYO']: #Assign key value: Solution Types to be used
            durationVolume[item] = 0
        ## Create appendixes to be used in MXDC Sample Name
        for t in times:
            for s in solvents:
                for cv in cryoVol:
                    for sv in solvVol:
                        #Case1: timely solvent duration test
                        if cryoVol == ['']:
                        #if len(cryoVol) == 0:
                            appendix = "{0}-{1}{2}nl".format(t, s, sv)
                            sDo = [t, s, sv]
                            cDo = False
                            print(appendix, sDo, cDo)
                        #Case2: timely cryopro duration test
                        elif solvents == [''] or solvVol == ['']:
                        #elif len(solvents)==0 or len(solvVol) == 0:
                            appendix = "{0}-Cryo{1}nl".format(t,cv)
                            sDo = False
                            cDo = [t,'CRYO',cv]
                        #Case3: solvent and cryopro duration test at one-time
                        elif times == ['']:
                        #elif len(times) == 0:
                            appendix = "Cryo{2}nl-{0}{1}nl".format(s, sv, cv)
                            sDo = [0, s, sv]
                            cDo = [0,'CRYO', cv]
                        #Case4 timely solvent and cryopro duration test
                        else:
                            appendix = "{0}-Cryo{3}nl-{1}{2}nl".format(t, s, sv, cv)
                            sDo = [t, s, sv]
                            cDo = [t,'CRYO', cv]
                        #By the Replication number
                        #write appendixes to be used in MXDC sample name
                        #write doSolvent and doCryopro to be used to write a echo worksheer
                        #calculate the volume of solutions users need to prepare
                        if replica == 1:
                            print(appendix)
                            appendixes.append(appendix)  
                            if sDo and solvents != [''] and solvVol != ['']:
                                doSolvent.append(sDo)
                                durationVolume[s] += float(sv)
                            if cDo and cryoVol != ['']:
                                doCryopro.append(cDo)
                                durationVolume['CRYO'] += float(cv)
                        else:
                            for i in range(int(self.replica.value())):
                                appendixes.append(appendix+'-{0}'.format(i+1))            
                                if sDo and solvents != [''] and solvVol != ['']:
                                    doSolvent.append(sDo)
                                    durationVolume[s] += float(sv)
                                if cDo and cryoVol != ['']: 
                                    doCryopro.append(cDo)
                                    durationVolume['CRYO'] += float(cv)

                                #if sDo: doSolvent.append(sDo)
                                #if cDo: doCryopro.append(cDo)
        ### Remove after stablized
        for i,item in enumerate(appendixes):
            try:
                if len(doSolvent) == 0:
                    print('[EvaluateWorksheet] sample name: {0} / toDo:{1}'.format(item, doCryopro[i]))
                elif len(doCryopro) == 0:
                    print('[EvaluateWorksheet] sample name: {0} / toDo:{1}'.format(item, doSolvent[i]))
                else:
                    print('[EvaluateWorksheet] sample name: {0} / toDo:{1}/{2}'.format(item, doSolvent[i], doCryopro[i]))
            except IndexError as e:
                print('[EvaluateWorksheer] please check your Evaluation plan:{0}'.format(e))
        
        ### Method duration user needs to prepare
        #self.requestMessage.append('This method requires {0} drops'.format(len(appendixes)))
        #for key, val in durationVolume.items():
        #    self.requestMessage.append('Requires {0}ul {1}'.format(val,key))
        return doSolvent, doCryopro, appendixes, durationVolume



    def makeEvalWorksheet(self):
        ### Final Result Form (Constant for this DeF)
        echoHeader = ['Harvest Time',\
                  'Source plate name', 'Source well', 'Transfer Volume', \
                  'Destination plate name', 'Targeting Well(forView)','Destination Well',\
                  'Destination Well X offset', 'Destination Well Y offset']

        shftHeader = [';PlateType', 'PlateID', 'LocationShifter', 'PlateRow', 'PlateColumn', 'PositionSubWell',\
                      'Comment', 'CrystalID', 'TimeArrival', 'TimeDeparture', 'PickDuration',\
                      'DestinationName', 'DestinationLocation', 'Barcode', 'ExternalComment']

        ### Variable Declaration
        plateType = self.PlateType.currentText()
        echoSolvList = [echoHeader]
        echoCryoList = [echoHeader]
        shifterList  = [shftHeader]
        username = misc.getUsername()
        prjid = adpt.usernameToID(username)
        DBrecord = []
        #Create Experiment ID
        explist = getMxlive.expOnMxlive()[::-1]
        EvalueExpID = func.createExpID('pretest', 'new',self.crystname.text(), explist)


        doSolvent, doCryopro, appendixes, durationVolume= self.scanEvalMethod()

        cnt = 0
        for key,val in self.targetPoints.items():
            try:
                #Assign A Chemical to key(imgKey)
                harvest = doSolvent[cnt][0]
                srcPlate = 'SourcePlate'
                srcType = doSolvent[cnt][1]
                srcWell = srcType
 
                destPlate = key.split('_')[0]
                destWell1 = key.split('_')[1]
                destWell2 = adpt.transPlate(plateType, destWell1)
                xtalRow = destWell1[0]
                xtalCol = str(int(destWell1[1:-1]))
                xtalSub = destWell1[-1]
                shftline = [plateType, destPlate, 'AM', xtalRow, xtalCol, xtalSub]
                shifterList.append(shftline)
                for i,item in enumerate(val):
                    #destPlate = key.split('_')[0]
                    #destWell1 = key.split('_')[1]
                    #destWell2 = adpt.transPlate(plateType, destWell1)
                    destYoffset = item[2]
                    if destWell1.endswith('d'): destXoffset = item[1] -700
                    else                      : destXoffset = item[1]
                    if doCryopro:
                        ctimes = float(doCryopro[cnt][2]) / 2.5 # 2.5ul : Minimum transfer value
                        cquota = ctimes // len(val)
                        cremain = ctimes % len(val)
                        if i+1 == len(val):
                            ctransVol = (cquota+cremain) * 2.5
                        else:
                            ctransVol = cquota * 2.5
                        echoCryoList.append( [doCryopro[cnt][0], srcPlate, doCryopro[cnt][1], ctransVol,\
                                          destPlate, destWell1, destWell2, destXoffset, destYoffset] )

                    if doSolvent:
                        #Assign Transfer volume to each value(points)
                        stimes = float(doSolvent[cnt][2]) / 2.5 # 2.5ul : Minimum transfer value
                        squota = stimes // len(val)
                        sremain = stimes % len(val)
                        if i+1 == len(val):
                            stransVol = (squota+sremain) * 2.5
                        else:
                            stransVol = squota * 2.5
                        echoSolvList.append( [harvest, srcPlate, srcWell, stransVol,\
                                             destPlate, destWell1, destWell2, destXoffset, destYoffset] )
                if doSolvent:
                    vol = doSolvent[cnt][2]
                    #if srcType.replace(' ' , '').lower() == 'dmso': smile = 'CS(=O)C'
                    #elif srcType.replace(' ' ,'').lower() == 'eg' or srcType == 'ethyleneglycol': smile = 'C(CO)O'
                    #else: smile = 'none'
                    if 'dmso' in srcType.replace(' ' , '').lower(): smile = 'CS(=O)C'
                    elif 'eg' in srcType.replace(' ' ,'').lower() or 'ethyleneglycol' in srcType.replace(' ' ,'').lower(): smile = 'C(CO)O'
                    else: smile = 'none' 
                elif doCryopro :
                    vol = doCryopro[cnt][2]
                    smile = 'none'
                else :
                    vol = 0
                    smile = 'none'
                record = { "name": username, \
                           "staff_comments": "upload by XtalViewer", "status": 5, "attachment": "null",\
                           "expri_id": EvalueExpID, "protein_name": self.crystname.text(), "plate_type": plateType,\
                           "plate_code": destPlate, "plate_imgpath": "none", "plate_well": destWell1,\
                           "plate_x": 0, "plate_y": 0, "crystal_no" : cnt+1,\
                           #Not matter in pretest
                           "soak_plate": 'pretest', "soak_well": 'Z00', "soak_vol": vol,\
                           "soak_id": appendixes[cnt], "soak_smile": smile, "project_id": prjid }
                DBrecord.append(record)
                cnt += 1
            except IndexError as e:
                pass
     
        echoPath = '{0}/{1}'.format(self.pathway['echo650'], username)
        shft1Path = '{0}/{1}'.format(self.pathway['shifter1'], username)
        shft2Path = '{0}/{1}'.format(self.pathway['shifter2'], username)
        try:
            if len(echoSolvList) > 1:
                misc.listToCsv(echoPath, EvalueExpID, echoSolvList)
                print(doSolvent)
                print('[saveWorkSheet] ECHO worksheet for solvent(s): {0}'.format(echoPath))
            else: pass
            if len(echoCryoList) > 1:
                misc.listToCsv(echoPath, EvalueExpID+'_Cryo', echoCryoList)
                print('[saveWorkSheet] ECHO worksheet for cryoprotectant: {0}'.format(echoPath))
            else: pass
        except OSError:
            print('[saveWorkSheet] No such directory: {0}'.format(echoPath))
        except FileNotFoundError:
            print('[saveWorkSheet] Cannot write a file in {0}'.format(echoPath))

        try:
            if len(shifterList) > 1:
                misc.listToCsv(shft1Path, EvalueExpID, shifterList)
                print('[saveWorkSheet] SHIFTER worksheet: {0}'.format(shft1Path))
            else: pass
        except OSError:
            print('[saveWorkSheet] No such directory: {0}'.format(shft1Path))
        except FileNotFoundError:
            print('[saveWorkSheet] Cannot write a file in {0}'.format(shft1Path))


        try:
            if len(shifterList) > 1:
                misc.listToCsv(shft2Path, EvalueExpID, shifterList)
                print('[saveWorkSheet] SHIFTER worksheet: {0}'.format(shft2Path))
            else: pass
        except OSError:
            print('[saveWorkSheet] No such directory: {0}'.format(shft2Path))
        except FileNotFoundError:
            print('[saveWorkSheet] Cannot write a file in {0}'.format(shft2Path))

        for item in echoSolvList:
            print('[saveWorkSheet]',item)
        for item in echoCryoList:
            print('[saveCryoSheet]', item)
        for item in shifterList:
            print('[shifterList]', item)
        for item in DBrecord:
            print(item)
        return EvalueExpID, DBrecord




    def giveUserMessage(self):
        sender = self.mainWidget.sender()
        #self.btnCheckEvalueMethod = QPushButton("Method Duration")
        #self.btnCheckScreenMethod = QPushButton("Method Duration")
        #self.btnCheckCryoMethod   = QPushButton("Method Duration")
        if self.btnCheckEvalueMethod == sender:
            doSolvent, doCryopro, appendixes, durationVolume = self.scanEvalMethod()
            self.requestMessage.append('This method requires {0} drops'.format(len(appendixes)))
            for key, val in durationVolume.items():
                self.requestMessage.append('Requires {0}ul {1}'.format(val,key))
        if self.btnCheckCryoMethod == sender:
            print('SENDER: duration for cryopeotectant')
        return 0

    def deliverToMxlive(self):
        sender = self.mainWidget.sender()
        if self.btnEvalueToMxlive == sender:
            expid, DBrecord = self.makeEvalWorksheet()
        elif self.btnScreenToMxlive == sender:
            expid, DBrecord = self.saveChemWorksheet()
        else: pass    

        jsonpath = func.deliverData(self.pathway, expid, DBrecord)
        for item in sorted(os.listdir(jsonpath)):
            if item.endswith('json'):
                jsonfile = '{0}/{1}'.format(jsonpath, item)
                data = putMxlive.upload_labworks('BL-5C', jsonfile)
                print('[uploadtoMxlive]',data)
        return 0


    def saveChemWorksheet(self):
        username = misc.getUsername()
        prjid = adpt.usernameToID(username)
        echoPath = '{0}/{1}'.format(self.pathway['echo650'], username)
        shft1Path = '{0}/{1}'.format(self.pathway['shifter1'], username)
        shft2Path = '{0}/{1}'.format(self.pathway['shifter2'], username)
        #if self.crystname.text().strip() == "":
        #    react = QMessageBox.warning(self,"Warning", "Give Protein name", QMessageBox.Yes)
        #else:
        #    defaultname = "target.csv"
        #    filesave = QFileDialog.getSaveFileName(self,"Save free target (csv)",\
        #                                           self.pathway['userhome']+'/Documents/'+defaultname, "CSV Files (*.csv)")
        #    if filesave[0] != '':
        #        if filesave[0].endswith('.csv'):
        #            filename = filesave[0]
        #        else:
        #            filename = filesave[0] + '.csv'
        #        targetList = self.makeChemWorksheet()
        #        with open(filename, 'w') as f:
        #            csvWriter = csv.writer(f)
        #            for line in targetList:
        #                csvWriter.writerow(line)


        if self.crystname.text().strip() == "":
            react = QMessageBox.warning(self,"Warning", "Give Protein name", QMessageBox.Yes)
            screenExpID, echo650List, shifterList, DBrecord = '', [], [], []
        else:
            screenExpID, echo650List, shifterList, DBrecord = self.makeChemWorksheet()
            print(screenExpID, echo650List, shifterList, DBrecord)
            if len(echo650List) <= 1:
                print('[saveWorksheet] There is no target to send to Liquid Handler')
            elif len(shifterList) <= 1:
                print('[saveWorksheet] There is no target to send to SHIFTERs')
            else: 
                try:
                    misc.listToCsv(echoPath, screenExpID, echo650List)
                    print('[saveWorkSheet] Save ECHO worksheet: {0}'.format(echoPath))
                except OSError as e:
                    print('[saveWorksheet] {0}'.format(e))
                    print('[saveWorkSheet] No such directory: {0}'.format(echoPath))
                except FileNotFoundError as e:
                    print('[saveWorksheet] {0}'.format(e))
                    print('[saveWorkSheet] Cannot write a file in {0}'.format(echoPath))
                try:
                    misc.listToCsv(shft1Path, screenExpID, shifterList)
                    print('[saveWorkSheet] Save SHIFTER worksheet: {0}'.format(shft1Path))
                except OSError as e:
                    print('[saveWorksheet] {0}'.format(e))
                    print('[saveWorkSheet] No such directory: {0}'.format(shft1Path))
                except FileNotFoundError as e:
                    print('[saveWorksheet] {0}'.format(e))
                    print('[saveWorkSheet] Cannot write a file in {0}'.format(shft1Path))
                try:
                    misc.listToCsv(shft2Path, screenExpID, shifterList)
                    print('[saveWorkSheet] Save SHIFTER worksheet: {0}'.format(shft2Path))
                except OSError as e:
                    print('[saveWorksheet] {0}'.format(e))
                    print('[saveWorkSheet] No such directory: {0}'.format(shft2Path))
                except FileNotFoundError as e:
                    print('[saveWorksheet] {0}'.format(e))
                    print('[saveWorkSheet] Cannot write a file in {0}'.format(shft2Path))

        return screenExpID, DBrecord






        return 0
    def makeChemWorksheet(self):
        ### Final Result Form (Constant for this DeF)
        echoHeader = ['Source plate name', 'Source well', 'Transfer Volume', \
                      'Destination plate name', 'Targeting Well(forView)','Destination Well',\
                      'Destination Well X offset', 'Destination Well Y offset']

        shftHeader = [';PlateType', 'PlateID', 'LocationShifter', 'PlateRow', 'PlateColumn', 'PositionSubWell',\
                      'Comment', 'CrystalID', 'TimeArrival', 'TimeDeparture', 'PickDuration',\
                      'DestinationName', 'DestinationLocation', 'Barcode', 'ExternalComment']

        ### Variabe Declaration
        username    = misc.getUsername()
        prjid       = adpt.usernameToID(username)
        explist     = getMxlive.expOnMxlive()[::-1]
        protein     = self.crystname.text().strip()
        screenExpID = func.createExpID('screen', 'new','noname', explist)
        plateType    = self.PlateType.currentText()
        echo650List  = [echoHeader]
        shifterList  = [shftHeader]
        DBrecord     = []
        if protein == "":
            react = QMessageBox.warning(self,"Warning", "Give Protein name", QMessageBox.Yes)
        
        else:
            screenExpID = func.createExpID('screen', 'new',protein, explist)
            ### Import User Input : from QLineEdit to List
            chemlist   = self.libraryInfo #It contains headerline
            chosenWell = self.getRegion()
            #Analyse header, ***make it case-insensitively!!
            #header = ['Vendor', 'Library', 'No', 'ID', 'Formula', 'MW', 'Smile', 'Conc_mM', 'Solvent', 'Plate_ID', 'Plate_well']
            idxPlateID = chemlist[0].index('Plate_ID')
            idxPlateWell = chemlist[0].index('Plate_well')
            idxChemID = chemlist[0].index('ID')
            idxSmile = chemlist[0].index('Smile')
            cnt = 0
            for key,val in self.targetPoints.items():
                try:
                    #Assign A Chemical to a key(imgKey)
                    idx = chosenWell[cnt]
                    srcPlate = chemlist[idx][idxPlateID]
                    srcWell  = chemlist[idx][idxPlateWell]
                    srcIDC   = chemlist[idx][idxChemID]
                    srcSmile = chemlist[idx][idxSmile]
                    totalVol = self.targetVol.value()
                    destPlate = key.split('_')[0]
                    destWell1 = key.split('_')[1]
                    destWell2 = adpt.transPlate(plateType, destWell1)
                    xtalRow = destWell1[0]
                    xtalCol = str(int(destWell1[1:-1]))
                    xtalSub = destWell1[-1]
                    shifterList.append( [plateType, destPlate, 'AM', xtalRow, xtalCol, xtalSub])
                    for i,item in enumerate(val):
                        destYoffset = item[2]
                        if destWell1.endswith('d'): destXoffset = item[1] -700
                        else                      : destXoffset = item[1]
                        #Assign Transfer volume to each value(points)
                        times = totalVol / 2.5 # 2.5ul : Minimum transfer value
                        quota = times // len(val)
                        remain = times % len(val)
                        if i+1 == len(val):
                            transVol = (quota+remain) * 2.5
                        else:
                            transVol = quota * 2.5
                        echo650List.append( [srcPlate, srcWell, transVol, destPlate, destWell1, destWell2, destXoffset, destYoffset] )

                    record = { "name": username, \
                               "staff_comments": "upload by XtalViewer", "status": 5, "attachment": "null",\
                               "expri_id": screenExpID, "protein_name": self.crystname.text(), "plate_type": plateType,\
                               "plate_code": destPlate, "plate_imgpath": "none", "plate_well": destWell1,\
                               "plate_x": 0, "plate_y": 0, "crystal_no" : cnt+1,\
                               "soak_plate": srcPlate, "soak_well": srcWell, "soak_vol": totalVol,\
                               "soak_id": srcIDC, "soak_smile": srcSmile, "project_id": prjid }
                    DBrecord.append(record)
                    cnt += 1
                except IndexError as e:
                    pass
            #remove it after stablized
            for item in echo650List:
                print('[makeChemWorksheet]',item)
            for item in shifterList:
                print('[makeChemWorksheet]',item)
            print('[makeChemWorksheet] {0} wells are Selected from the current library: {1}'.format(len(chosenWell), chosenWell))
        return screenExpID, echo650List, shifterList, DBrecord


    def makeCryoWorksheet(self):
        return 0
    def sendToInstrument(self):
        return 0
    def uploadToWebDB(self):
        return 0

    def clearChemPoints(self):
        return 0
    def clearCryoPoints(self):
        return 0



    def workSheets(self):
        #explist = [item for item in self.expOnWeb if item.startswith('Pre')]
        explist = getMxlive.expOnMxlive()[::-1]
        EvalueExpID = func.createExpID('pretest', 'new',self.crystname.text(), explist)
        print('[checkEXPID]', EvalueExpID)
    def shft1Finish(self):
        jsonpath = func.shft1Done(self.xtalsToScreen, self.ScreenExpID.currentText(), self.ScreenProtein.text(),self.pathway)
        for item in sorted(os.listdir(jsonpath)):
            if item.startswith(self.ScreenExpID.currentText()) and item.endswith('json'):
                jsonfile = '{0}/{1}'.format(jsonpath, item)
                data = putMxlive.upload_labworks('BL-5C', jsonfile)
                print(data)
            else: pass

    def shft2Finish(self):
        jsonpath = func.shft2Done(self.xtalsToScreen, self.ScreenExpID.currentText(), self.ScreenProtein.text(),self.pathway)
        for item in sorted(os.listdir(jsonpath)):
            if item.startswith(self.ScreenExpID.currentText()) and item.endswith('json'):
                jsonfile = '{0}/{1}'.format(jsonpath, item)
                data = putMxlive.upload_labworks('BL-5C', jsonfile)
                print(data)
            else: pass





    def imageClickEvent(self, event):
        #text = "LeftClick: x={0}, y={1}, global = {2},{3}".format(event.x()-zero[0], event.y()-zero[1], event.globalX(), event.globalY())
        try:
            rowXoffset = int( self.currentWell.text()[1:3] ) * self.colOffset[0] - self.colOffset[0]
            rowYoffset = int( self.currentWell.text()[1:3] ) * self.colOffset[1] - self.colOffset[1]
            colXoffset = ( ord(self.currentWell.text()[0]) - 65 ) * self.rowOffset[0]
            colYoffset = ( ord(self.currentWell.text()[0]) - 65 ) * self.rowOffset[1]
            Xoffset = rowXoffset + colXoffset
            Yoffset = rowYoffset + colYoffset

            zero = [ (self.guideShift1[0]+self.guideRadius+Xoffset) * self.imgScale.value()/100,\
                     (self.guideShift1[1]+self.guideRadius+Yoffset) * self.imgScale.value()/100 ]

            xPixcelPosition = (event.x()-zero[0]) / (self.imgScale.value()/100)
            yPixcelPosition = (event.y()-zero[1]) / (self.imgScale.value()/100)
            xMeterPosition = (event.x()-zero[0]) * self.wellRadius / (self.guideRadius * self.imgScale.value() /100)
            yMeterPosition = (event.y()-zero[1]) * self.wellRadius / (self.guideRadius * self.imgScale.value() /100)

            pID = self.currentPlate.text()
            well = self.currentWell.text()
            targetSites = self.divisions.value()
            imgKey = '{0}_{1}'.format(pID,well)
            if event.buttons() & Qt.LeftButton:
                if self.eraserFlag:
                    self.distanToPoint(xPixcelPosition,yPixcelPosition)
                    self.undoButton.toggle()
                    try:
                        text = 'Remove a point: {0} point(s) in {1}@{2}'.format(len(self.targetPoints[imgKey]),well,pID)
                        print('[EraserGo]', len(self.targetPoints[imgKey]))
                        
                        #self.undoButton.toggle()
                        #self.eraserFlag = False
                        if len(self.targetPoints[imgKey]) == 0:
                            self.targetPoints.pop(imgKey)
                            self.window.nWells.setText( str(len(self.targetPoints)).rjust(4,' ') )
                    except KeyError as e:
                        text = 'No point(s) yet'
                else:
                    self.scale.append([round(xMeterPosition,0),round(yMeterPosition,0), xPixcelPosition, yPixcelPosition])
                    if len(self.scale) == 1:
                        text = "LeftClick: x={0}um, y={1}um in {2}_{3}".format(round(xMeterPosition,0), round(yMeterPosition,0), pID, well)
                    elif len(self.scale) == 2:
                        x1 = self.scale[0][0]
                        y1 = self.scale[0][1]
                        x2 = self.scale[1][0]
                        y2 = self.scale[1][1]
                        distance = (abs(x1-x2)**2 + abs(y1-y2)**2)**(1/2)
                        #text = "LeftClick: x={0}um, y={1}um in {2}_{3} // Distance: {4}um".format(round(xMeterPosition,0), round(yMeterPosition,0), pID, well, round(distance,0))
                        text = "LeftClick: x={0}um, y={1}um in {2}_{3}".format(round(xMeterPosition,0), round(yMeterPosition,0), pID, well)
                        self.window.distance.setText(str(round(distance)).rjust(4,' ')+' um')
                        self.scale = []
                    else:
                        self.scale.append([round(xMeterPosition,0),round(yMeterPosition,0), xPixcelPosition, yPixcelPosition])
                        text = "LeftClick: x={0}um, y={1}um in {2}_{3}".format(round(xMeterPosition,0), round(yMeterPosition,0), pID, well)
                    print("LeftClick: x={0}um, y={1}um [ x={2}px, y={3}px]".format(round(xMeterPosition,0), round(yMeterPosition,0), xPixcelPosition, yPixcelPosition))
                self.window.statusbar.showMessage(text)
                if self.eraserFlag == True:
                    #self.undoButton.toggle()
                    self.eraserFlag = False
         

                #try:
                #    print('remain', self.targetPoints[imgKey])
                #except KeyError:
                #    print('KeyError:{0}'.format(imgKey))
            if event.button() & Qt.RightButton:
                #if self.chooseLib.currentText() == 'none':
                text = "RightClick: x={0}um, y={1}um in {2}_{3}".format(round(xMeterPosition,0), round(yMeterPosition,0), pID, well)
                po = ['nonetype', round(xMeterPosition,0),round(yMeterPosition,0), xPixcelPosition, yPixcelPosition]
                self.window.statusbar.showMessage(text)
                #search imgkey in self.targetPoints's keys
                if imgKey in self.targetPoints.keys():
                    self.targetPoints[imgKey].append( po )
                else: 
                    self.targetPoints[imgKey] = [ po ]
                self.loadImage()
                targetsInThisWell = len( self.targetPoints[imgKey] )
                if targetsInThisWell >= targetSites:
                    print(targetsInThisWell ,self.divisions.value())
                    self.moveNextWell()
                else: pass
                for key,val in self.targetPoints.items():
                    print(key,val)
                self.window.nWells.setText( str(len(self.targetPoints)).rjust(4,' ') )


        except ValueError as e:
            #Many variables depended in Well Image occur ValueError
            pass



if __name__== '__main__':
    import sys
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    #ex = MyApp()
    ex2 = index()
    sys.exit(app.exec_())

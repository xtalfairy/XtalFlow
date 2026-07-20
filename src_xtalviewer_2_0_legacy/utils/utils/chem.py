import os
import csv
import getpass
import datetime
from . import misc

from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Chem import Draw



def drawFormular(chemName, chemSmile, imgPath):
    misc.createFolder(imgPath)
    smile = Chem.MolFromSmiles(chemSmile)
    imgName = '{0}/{1}.png'.format(imgPath, chemName)
    AllChem.Compute2DCoords(smile)
    Draw.MolToFile(smile, imgName)

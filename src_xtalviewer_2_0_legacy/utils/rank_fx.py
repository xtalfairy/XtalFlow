import numpy
import cv2
from skimage import morphology, measure
from scipy import ndimage, stats
from numpy import matlib
from PIL import Image
import imgm


def translate_image(image, t):
    T = numpy.float32([1, 0,t[0]],[0,1,t[1]])
    #
    trans = T[0:2, : ]
    inv_t = numpy.linalg.inv(T)
    inv_trans = inv_t[0:2, :]
    h,w = image.shape[:2]

    # Transfrom the 4 corners of the inumpyut image
    src_pts = numpy.float32([[0, 0], [w-1, 0], [0, h-1], [w-1, h-1]]) # https://stackoverflow.com/questions/44378098/trouble-getting-cv-transform-to-work (see comment).
    dst_pts = cv2.transform(numpy.array([src_pts]), trans)[0]
    
    min_x, max_x = numpy.min(dst_pts[:, 0]), numpy.max(dst_pts[:, 0])
    min_y, max_y = numpy.min(dst_pts[:, 1]), numpy.max(dst_pts[:, 1])
    
    # Destination matrix width and height
    dst_w = int(max_x - min_x + 1) # 895
    dst_h = int(max_y - min_y + 1) # 384
    
    # Inverse transform the center of destination image, for getting the coordinate on the source image.
    dst_center = numpy.float32([[(dst_w-1.0)/2, (dst_h-1.0)/2]])
    src_projected_center = cv2.transform(numpy.array([dst_center]), inv_trans)[0]
    
    # Compute the translation of the center - assume source center goes to destination center
    translation = src_projected_center - numpy.float32([[(w-1.0)/2, (h-1.0)/2]])
    
    # Place the translation in the third column of trans
    trans[:, 2] = translation
    
    # Transform
    dst_im = cv2.warpAffine(src_im, trans, (dst_w, dst_h))

    return dst_im

def DroplITChanged(im, bw)
    strelDisk2 = morphology.disk(2)
    strelDisk3 = morphology.disk(3)
    strelDisk7 = morphology.disk(7)
    strelDisk10 = morphology.disk(10)
    strelDisk20 = morphology.disk(20)

    a = im
    _a = a.sum(axis=0)
    a = _a
    a_height = a.shape[0]
    a_width = a.shape[1]
    b = cv2.resize(a, dsize=(0,0), fx=0.15, fy=0.15 ,interpolation=cv2.INTER_LINEAR)
    bsav = b
    _bw = cv2.resize(bw, dsize=(0,0), fx=3.0, fy=3.0 ,interpolation=cv2.INTER_CUBIC)
    bw = _bw
    #cv2.morphologyEx(b, cv2.MORPH_CLOSE, strelDisk3))
    #matlab
    f=edge(imclose(b,strelDisk3),'zerocross')
    bwSmall = cv2.erode(bwT, strelDisk3)
    bw = cv2.erode(bw, strelDisk3)

    f = numpy.multiply(bwsmall, f)
    if f.sum() == 0:
        f_height = f.shape[0]
        f_width = f.shape[1]
        f[f_width/2][f_height/2] =1
    bx = cv2.morphologyEx(b, cv2.MORPH_CLOSE, strelDisk3)
    bdifindx = (bx-b) > 30
    bcopydiff = numpy.zeros_like(b)
    bcopydiff[bdifindx] = 1
    _bcopydiff = cv2.dilate(bcopydiff, strelDisk3, iterations = 1)
    bcopydiff = _bcopydiff
    b[bdifindx] = bx[bdifindx]
    bx = b
    sumx = numpy.sum(f, axis=0) #vertical factors
    sumy = numpy.sum(f, axis=1) #horizontal factors
    sumtot = sum(sumx)
    sumMomx = numpy.multiply( [i for i in range(1,len(sumx)+1)], sumx )
    sumMomy = numpy.multiply( [j for j in range(1,len(sumy)+1)], sumy )
    xPos = sum(sumMomx) / sumtot
    yPos = sum(sumMomy) / sumtot

    massed = numpy.multiply(bw, bsac)
    _massed = cv2.morphologyEx(massed, cv2,MORPH_CLOSE, strelDisk7) - massed
    __massed = _massed
    __massed[_massed < 5] = 0
    massed = __massed
    sumx = numpy.sum(massed, axis=0)
    sumy = numpy.sum(massed, axis=1)
    sumtot = sum(sumx)
    sumMomx = numpy.multiply( [i for i in range(1,len(sumx)+1)], sumx )
    sumMomy = numpy.multiply( [j for j in range(1,len(sumy)+1)], sumy )
    xPosd = sum(sumMomx) / sumtot
    xPosd = sum(sumMomy) / sumtot
    
    xPos = xPos+xPosd
    yPos = yPos+yPosd
    
    bxchunck = cv2.morphologyEx(bx, strelDisk20)-bx
    bchuncklabel = measure.label( imgm.bwmorph_thin( cv2.morphologyEx(f, strelDisk2) ) )
    bchuncklabelpros = measure.regionprops(bchuncklabel, 'ConvexArea')
    idx = bchuncklabelpros.ConvexArea < 10000
    BW2 = bchuncklabel == idx
    BW2 = BW2/1
    bwchum = ndimage.binary_fill_holes(BW2)
    cv2.erode(bwchum , numpy.ones((3,3)))

    bxchunck = cv2.morphologyEx(bx, strelDisk20)-bx
    tothre = (numpy.max(bxchunck)-bxchunck)/numpy.max(bxchunck)
    try:
        _level, _BWs = cv2.threshold(tothre, 0,255, cv2.THRESH_OTSU)
        #level = _level/tothre.max()
        level, BWs = cv2.threshold(tothres, _level*4/_BWs.max(), 1, cv2.THRESH_BINARY)

    except:
        BWs = zeros(b.shape)

    #UNTRANSLATED MATLAB
    bx = edgeGradient(bx, 'canny', [], 0.5).*bw
    
    if bx.any() > 0:
        bxidx = cv2.dilate(bx, strelDisk3)
        bx(bxidx) = bx.mean()
    if bwchum.any() > 0:
        bwchumidx = cv2.dilate(bwchum, strelDisk2)
        bwchum(bwchumidx) = bx.mean()
    bxidx = (cv2.dilate((1-bw), strelDisk2)-bw) == numpy.zeros_like(bw)
    bx(bxidx)=stats.mstats.mquantiles(bx, 0.5, alphap=0.5, betap=0.5)
    
    qyp = stats.mstats.mquantiles(bx, 0.995)
    bxqidx = ((bx > qyp)/1).astype('int')
    bx[bxqidx]=qyp


    ThetaNum = 360
    rhoMax   = 250
    thetac = matlib.repmat( numpy.linspace(0,2*3.1416, ThetaNum), rhoMax, 1)
    rhoc   = matlib.repmat( numpy.linspace(1,rhoMax,rhoMax), 1, ThetaNum)
    lin_thetac = thetac.swapaxes(0,1).reshape(ThetaNum*rhoMax,1)
    lin_rhoc   = rhoc.swapaxes(0,1).reshape(ThetaNum*rhoMax,1)
    x,y = imgm.pol2cart(lin_thetac, lin_rhoc) 
    X = x.reshape(ThetaNum,rhoMax).swapaxes(0,1)
    Y = y.reshape(rhoMax,ThetaNum).swapaxes(0,1)
    X = X+xPos
    Y = Y+yPos
    Xi,Yi = np.meshgrid(np.linspace(1,f.shape[0],f.shape[0]),np.linspace(1,f.shape[1],f.shape[1]))
    Z = bx
    ZI = interp2(np.linspace(1,f.shape[0],f.shape[0]), np.linspace(1,f.shape[1],f.shape[1]), Z, kind='cubic')
    ZIZerosIndx = (abs(ZI) < 0.001) | ~(numpy.isfinite(ZI))
    #Zzeros = numpy.zeros_like(ZI)
    #Zzeros[ZIZerodIndx] = 1
    #ZIZeroIDX = (Zzeros > 0)
    #ZI[ZIZeroIDX] = 0
    ZI[ZIZerosIndx] = 0
    ZI[-1] = 0
    Zgard = ZI
    Zgard = numpy.max(Zgard) - Zgard
    Zgard = matlib.repmat(Zgard, 1,3)
    #Zgard = scipy.misc.imresize(bwTer, (Zgard.shape[0]*2,Zgard.shape[1]*2), 'nearest')
    Zgard = numpy.array(Image.fromarray(bwTer).resize( (Zgard.shape[0]*2,Zgard.shape[1]*2), Image.NEAREST)     

    parentI = numpy.zeros_like(Zgard)
    distI = Zgard
    weighFactDistUng = numpy.max(Zgard)
    wighPrefact = 0.2

    for h in range(1,Zgard.shape[1]):
        for j in range(0,Zgard.shape[0]):
            if j == 0:
                distId  = dist[j][h-1]+Zgard[j][h]+wighPrefact*1*weighFactDistInt
                distId1 = min( distId, distI[j+1][h-1]+Zgard[j][h]+wighPrefact*1.41*weighFactDistInt )
                distI[j][h] = distId1
                if distId1 < distId: maxindx = 3
                else: maxindx = 2
                parentI[j][h] = maxindx
            elif j > 1 and j < Zgard.shape[0]-1: 
                distId  = dist[j-1][h-1]+Zgard[j][h]+wighPrefact*1.41*weighFactDistInt
                distId1 = min( distId, distI[j][h-1]+Zgard[j][h]+wighPrefact*1*weighFactDistInt )
                distId3 = min( distId, distI[j+1][h-1]+Zgard[j][h]+wighPrefact*1.41*weighFactDistInt )
                distI[j][h] = distId3
                #maxindx = 1
                if distId3 < distId1: maxindx = 3
                elif distId1 < distId: maxindx = 2
                else: maxindx = 1
                parentI[j][h] = maxindx
            else:
                distId = distI[j-1][h-1]+Zgard[j][h]+wighPrefact*1.41*weighFactDistInt
                distId1 = min( distId, distI[j][h-1]+Zgard[j][h]+wighPrefact*1*weighFactDistInt )
                distI[j][h] = distId1
                if distId1 < distId: maxindx = 1
                else: maxindx = 2
                parentI[j][h] = maxindx
                
    dfsfd = min(distI[:, -1])  
    parentnode = numpy.argmin(distI[:, -1])
    parentI[parentnode,Zgard.shape[1]] = 0

    for h in range(Zgard.shape[1]-2, 0, -1):
        parentnode = min( max( 1, parentnode+parentI[parentnode, h]-2 ), Zgard.shape[0] )  
        parentI[parentnode,h] = 0
    Zgard = Zgard[:,2*ZI.shape[1]+1:4*ZI.shape[1]+1]
    parentI = parentI[:,2*ZI.shape[1]+1:4*ZI.shape[1]+1]

    
    Zgard = numpy.array(Image.fromarray(Zgard).resize( (Zgard.shape[0],Zgard.shape[1]/2), Image.NEAREST))
    parentI = numpy.array(Image.fromarray(parentI).resize( (parentI.shape[0], parentI.shape[1]/2), Image.NEAREST))

    indpath = parentI[parentI>0]
    rPath, thetaPath = imgm.ind2sub(Zgard.shape, indpath)
    #I'm worried about the zeros in rpath and thethpath
    xpath = numpy.multiply(rPath, imgm.cosarr(thetaPath))
    ypath = numpy.multiply(rPath, imgm.sinarr(thetaPath))

    towrite = numpy.zeros_like(f)
    inxdsd = (imgm.roundA(ypath+yPos)>0) & (imgm.roundA(xpath+xPos)>0) &\
             (imgm.roundA(xpath+xPos)<towrite.shape[1]) & (imgm.roundA(ypath+yPos)<towrite.shape[0])
    towrite = lindexF(towrite, imgm.roundA(ypath[inxdsd]+yPos), imgm.roundA(xpath[inxdsd]+xPos), 1)
    towrite = cv2.dilate(towrite, strelDisk2)
    towrite = imgm.bwmorph_thin(towrite)     

    if towrite.sum() == 0:
        region = None
    else:
        region = imgm.imclearborder(towrite)


def edgeGradient(eout, thresh, gv_45, gh_135):
    /

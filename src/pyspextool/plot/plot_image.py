import os
import numpy as np
import matplotlib.pyplot as pl

from pyspextool.fit.polyfit import poly_1d
from pyspextool.io.check import check_parameter
from pyspextool.plot.limits import get_image_range


def plot_image(image, mask=None, orders_plotinfo=None, trace_plotinfo=None,
               locateorders_plotinfo=None, file_info=None, plot_size=(5, 5)):
    """
    To plot a spectral image along with the edges and order numbers

    Parameters
    ----------
    image : numpy.ndarray
        An (nrows, ncols) float array with (cross-dispersed) spectral orders.  
        It is assumed that the dispersion direction is roughly aligned 
        with the rows of `img` and the spatial axis is roughly aligned 
        with the columns of `img.  That is, orders go left-right and 
        not up-down. 

    mask : numpy.ndarray, optional
        An (nrows, ncols) array where "bad" pixels are set and "good" pixels
        are zero. If passed, bad pixels are colored red.

    orders_plotinfo : dict, optional

        `'edgecoeffs'` : numpy.ndarray 
            (norders,`edgedeg`+1,2) float array giving the polynomial 
            coefficients delineating the top and bottom of each order.  
            edgecoeffs[0,0,:] gives the coefficients for the bottom of the 
            order closest to the bottom of the image and edgecoeffs[0,1,:] 
            gives the coefficients for the top of said order.  

        `'xranges'` : array_like
            An (norders, 2) float array giving the column numbers over which to 
            operate.  xranges[0,0] gives the starting column number for the 
            order nearest the bottom of the image and xranges[0,1] gives 
            the end column number for said order.

        `'orders'` : list of int
            (norders,) int array of the order numbers.  By Spextool convention, 
            orders[0] is the order closest to the bottom of the array.

    trace_plotinfo : dict, optional
        Later

    locateorders_plotinfo : dict, optional
        Later

    file_info : dict, optional
        `"figsize"` : tuple
            (2,) tuple of the figure size (inches).

        `"filepath"` : str
            The directory to write the QA figure.

        `"filename"` : str
            The name of the file, sans suffix/extension.

        `"extension"` : str
            The file extension.  Must be compatible with the savefig
            function of matplotlib.

    plot_size : tuple, default=(5,5)
        A (2,) tuple giving the figure size.

    Returns
    -------
        None
    
    """

    #
    # Check parameters
    #

    check_parameter('plot_image', 'image', image, 'ndarray', 2)

    check_parameter('plot_image', 'mask', mask, ['NoneType', 'ndarray'], 2)

    check_parameter('plot_image', 'locateorders_plotinfo',
                    locateorders_plotinfo, ['NoneType', 'dict'])

    check_parameter('plot_image', 'orders_plotinfo', orders_plotinfo,
                    ['NoneType', 'dict'])

    check_parameter('plot_image', 'trace_plotinfo', trace_plotinfo,
                    ['NoneType', 'dict'])

    check_parameter('plot_image', 'file_info', file_info, ['NoneType', 'dict'])

    check_parameter('plot_image', 'plot_size', plot_size, ['NoneType', 'tuple'])

    if file_info is None:

        doplot(image, plot_size, mask=mask,
               locateorders_plotinfo=locateorders_plotinfo,
               orders_plotinfo=orders_plotinfo, trace_plotinfo=trace_plotinfo)

        pl.show()
        pl.close()

    else:

        doplot(image, file_info['figsize'], mask=mask,
               locateorders_plotinfo=locateorders_plotinfo,
               orders_plotinfo=orders_plotinfo,
               trace_plotinfo=trace_plotinfo)

        pl.savefig(os.path.join(file_info['filepath'], file_info['filename'] + \
                                file_info['extension']))
        pl.close()


def doplot(image, figsize, mask=None, locateorders_plotinfo=None,
           orders_plotinfo=None, trace_plotinfo=None):
    """
    To plot the image independent of the device

    """

    # Set the color map

    minmax = get_image_range(image, 'zscale')

    cmap = pl.cm.gray

    # Now check to see if the mask is passed.

    pimage = np.copy(image)
    if mask is not None:
        cmap.set_bad((1, 0, 0, 1))
        z = np.where(mask == 1)
        pimage[z] = np.nan

    # Now draw the figure

    fig = pl.figure(figsize=figsize)
    pl.imshow(pimage, vmin=minmax[0], vmax=minmax[1], cmap=cmap,
              origin='lower')
    pl.xlabel('Columns (pixels)')
    pl.ylabel('Rows (pixels)')

    #
    # Overplot orders if requested
    #

    if orders_plotinfo is not None:

        xranges = orders_plotinfo['xranges']
        edgecoeffs = orders_plotinfo['edgecoeffs']
        orders = orders_plotinfo['orders']
        norders = len(orders)

        for i in range(norders):
            x = np.arange(xranges[i, 0], xranges[i, 1] + 1)
            bot = poly_1d(x, edgecoeffs[i, 0, :])
            top = poly_1d(x, edgecoeffs[i, 1, :])

            pl.plot(x, bot, color='purple')
            pl.plot(x, top, color='purple')
            pl.fill_between(x, bot, y2=top, color='purple', alpha=0.15)

            delta = xranges[i, 1] - xranges[i, 0]
            idx = np.fix(delta * 0.02).astype(int)

            pl.text(x[idx], (top[idx] + bot[idx]) / 2., str(orders[i]),
                    color='yellow', verticalalignment='center')

    #
    # Overplot traces if requested
    #

    if trace_plotinfo is not None:

        if 'x' in trace_plotinfo and \
                'y' in trace_plotinfo and \
                'goodbad' in trace_plotinfo:
            pl.plot(trace_plotinfo['x'], trace_plotinfo['y'], 'go', markersize=2)
            bad = trace_plotinfo['goodbad'] == 0
            pl.plot(trace_plotinfo['x'][bad], trace_plotinfo['y'][bad], 'bo',
                    markersize=2)

        if 'fits' in trace_plotinfo:

            for i in range(len(trace_plotinfo['fits'])):
                pl.plot(trace_plotinfo['fits'][i][0, :],
                        trace_plotinfo['fits'][i][1, :], color='cyan',
                        linewidth=0.5)

    #
    # Overplot order locating data if requested.
    #

    if locateorders_plotinfo is not None:

        guess = locateorders_plotinfo['guesspos']
        x = locateorders_plotinfo['x']
        y = locateorders_plotinfo['y']
        goodbad = locateorders_plotinfo['goodbad']
        coeffs = locateorders_plotinfo['coeffs']

        for i in range(len(guess)):
            pl.plot(guess[i][0], guess[i][1], 'go')

        for i in range(len(x)):
            pl.plot(x[i], y[i], 'go', markersize=1)

            bad = goodbad[i] == 0
            pl.plot(x[i][bad], y[i][bad], 'bo', markersize=1)

            pl.plot(x[i], np.polynomial.polynomial.polyval(x[i], coeffs[i]),
                    'r-', linewidth=0.3)

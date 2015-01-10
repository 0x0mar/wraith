#!/usr/bin/env python

""" bits: bit related functions

defines
 bitmask related functions operating on bitmasks defined as dicts of the form
 name->mask where name (string) is represented by mask (integer) i.e
 {'flag1':(1 << 0),...,'flag2':(1 << 4)} or {'flag1':1,...,'flag2':16}
 bit extraction functions
"""

__name__ = 'bits'
__license__ = 'GPL'
__version__ = '0.0.4'
__date__ = 'November 2014'
__author__ = 'Dale Patterson'
__maintainer__ = 'Dale Patterson'
__email__ = 'wraith.wireless@hushmail.com'
__status__ = 'Development'

def bitmask(bm,mn):
    """ returns a list of names defined in bitmask bm that are present in mn """
    if mn == 0: return []
    return [name for name,mask in bm.items() if mn & mask == mask]

def bitmask_list(bm,mn):
    """ returns a dict d = {name:is_set} for each name in bm and value in mn """
    d = {}
    for name in bm: d[name] = int(bm[name] & mn == bm[name])
    return d

def bitmask_get(bm,mn,f):
    """
     returns True if flag f in bitmask bm is set in magic number mn
     throws KeyError if f is not defined in bitmask
    """
    return int(bm[f] & mn == bm[f])

def bitmask_set(bm,mn,f):
    """
     sets the flag in mn (as defined by bm to True (or 1)
     throws KeyError if f is not defined in bitmask
    """
    return mn | bm[f]

def leastx(x,v):
    """ returns the (unsigned int) value of the x least most significant bits in v """
    return v & ((1 << x) - 1)

def midx(s,x,v):
    """ returns the (unsigned int) value of x bits starting at s from v """
    return v & (((1 << x) - 1) << s)

def mostx(s,v):
    """ returns the (unsigned int) value of the most significant bits in v starting at s """
    return v >> s
"""job_stream's python module implementation.  Example usage:

class AddOne(job_stream.Job):
    def handleWork(self, work):
        self.emit(work + 1)

job_stream.work.append(8)
job_stream.work.append(9)
job_stream.run({
    'jobs': [
        { 'type': AddOne }
    ]
})
# 9 and 10 will have been printed.
"""

import _job_stream as _j

import cPickle as pickle
import multiprocessing

def _decode(s):
    """Decodes an object with cPickle"""
    return pickle.loads(s)


def _encode(o):
    """Encodes an object with cPickle"""
    return pickle.dumps(o)


class Object(object):
    """A generic object with no attributes of its own."""


# Initialize the encode and decode values first so that they can be used in
# debug code (if left uninitialized, any attempt to pickle something from within
# C++ code will crash with NoneType cannot be called)
_j.registerEncoding(Object, _encode, _decode)


class _Work(list):
    """List of initial work sent into job_stream.
    If left empty, work comes from stdin."""
work = _Work()


class Job(_j.Job):
    """Base class for a standard job (starts with some work, and emits zero or
    more times).  Handles registration of job class with the job_stream
    system."""
    class __metaclass__(type(_j.Job)):
        def __init__(cls, name, bases, attrs):
            type(_j.Job).__init__(cls, name, bases, attrs)

            # Derived hierarchical name, use that in config
            fullname = cls.__module__
            if fullname == '__main__':
                fullname = name
            else:
                fullname += '.' + name

            # Metaclasses are called for their first rendition as well, so...
            if fullname == 'job_stream.Job':
                return

            _j.registerJob(fullname, cls)


    def postSetup(self):
        """Called when self.config is set and the Job is fully ready for work,
        but before any work is accepted."""
        pass


    def handleWork(self, work):
        """Handle incoming work, maybe call self.emit() to generate more work
        for jobs further down the pipe."""
        raise NotImplementedError()



class Reducer(_j.Reducer):
    """Base class for a Reducer (starts with...
    TODO
    """
    class __metaclass__(type(_j.Reducer)):
        def __init__(cls, name, bases, attrs):
            type(_j.Reducer).__init__(cls, name, bases, attrs)

            # Derived hierarchical name, use that in config
            fullname = cls.__module__
            if fullname == '__main__':
                fullname = name
            else:
                fullname += '.' + name

            # Metaclasses are called for their first rendition as well, so...
            if fullname == 'job_stream.Reducer':
                return

            _j.registerReducer(fullname, cls)


    def postSetup(self):
        """Called when self.config is set and the Job is fully ready for work,
        but before any work is accepted."""
        pass


    def handleInit(self, store):
        """Called when a reduction is started.  Store is a python object that
        should be modified to remember information between invocations."""


    def handleAdd(self, store, work):
        """Called when new work arrives at the Reducer."""
        raise NotImplementedError()


    def handleJoin(self, store, other):
        """Called to merge two stores from the same Reducer."""
        raise NotImplementedError()


    def handleDone(self, store):
        """Called when the reduction is finished.  The reduction will be marked
        as unfinished if a recur() happens."""
        raise NotImplementedError()



class Frame(_j.Frame):
    """Base class for a Frame
    TODO
    """
    class __metaclass__(type(_j.Frame)):
        def __init__(cls, name, bases, attrs):
            # Derived hierarchical name, use that in config
            fullname = cls.__module__
            if fullname == '__main__':
                fullname = name
            else:
                fullname += '.' + name

            # Metaclasses are called for their first rendition as well, so...
            if fullname == 'job_stream.Frame':
                return

            _j.registerFrame(fullname, cls)


    def handleFirst(self, store, work):
        """Called for the first work, which starts a reduction.  Store is an
        empty Object() to which this method may assign attributes."""
        raise NotImplementedError()


    def handleNext(self, store, work):
        """Called when finished work arrives at the Frame."""
        raise NotImplementedError()


    def handleDone(self, store):
        """Called when the reduction is finished.  The reduction will be marked
        as unfinished if a recur() happens."""
        raise NotImplementedError()


    def postSetup(self):
        """Called when self.config is set and the Frame is fully ready for work,
        but before any work is accepted."""



def run(yamlPath):
    """Runs the given YAML file."""
    cpuCount = multiprocessing.cpu_count()
    _j.runProcessor(yamlPath, work)

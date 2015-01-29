
from .common import ExecuteError, JobStreamTest
import tempfile
import os

class TestInline(JobStreamTest):
    def test_checkpoint(self):
        # Ensure that results are saved across checkpoints
        chkpt = os.path.join(tempfile.gettempdir(), "test.chkpt")
        try:
            os.remove(chkpt)
        except OSError, e:
            if e.errno != 2:
                raise
        src = """
        import job_stream.inline as inline
        import os
        import tempfile
        chkpt = os.path.join(tempfile.gettempdir(), "test.chkpt")

        w = inline.Work([ 1, 2, 3 ] * 10)
        w.job(inline._ForceCheckpointJob)

        # w.run() isn't a generator.  It blocks until EVERYTHING is done.  So, this is
        # only executed on success
        for r in w.run(checkpointFile = chkpt, checkpointSyncInterval = 0):
            print repr(r)
"""
        # Run it often; each time should output nothing, and final should have all
        for _ in range(30):
            try:
                r = self.executePy(src)
                break
            except ExecuteError, e:
                self.assertEqual("", e.stdout)
        self.assertLinesEqual("1\n2\n3\n" * 10, r[0])


    def test_frame(self):
        # Ensure a frame works...
        r = self.executePy("""
import job_stream.inline as inline
work = inline.Work([ 3, 8, 9 ])

@work.frame(store = lambda: inline.Object(total = 1), emit = lambda store: store.total)
def nextPowerOfTwo(store, first):
    if store.total < first:
        return inline.Multiple([ store.total ] * store.total)

@work.job
def a(w):
    return 1

@work.frameEnd
def nextPowerOfTwo(store, next):
    store.total += next

for r in work.run():
    print repr(r)
""")
        self.assertLinesEqual("4\n8\n16\n", r[0])


    def test_init(self):
        # Ensure that @init works
        self.safeRemove("blah.chkpt")
        self.safeRemove("blah")
        try:
            cmd = """
import job_stream.inline as inline
work = inline.Work([ 3, 8, 9 ])

@work.init
def setup():
    with open('blah', 'a') as f:
        f.write("1")

# Force a checkpoint so that setup() will interpret the application as already
# started.
work.job(inline._ForceCheckpointJob)

@work.job
def failure(w):
    raise ValueError("HUHM?")

work.run(checkpointFile = "blah.chkpt", checkpointSyncInterval = 0)"""

            with self.assertRaises(ExecuteError):
                self.executePy(cmd)
            with self.assertRaises(ExecuteError):
                self.executePy(cmd)
            with self.assertRaises(ExecuteError):
                self.executePy(cmd)

            # Should have been run exactly once
            self.assertTrue(os.path.lexists("blah.chkpt"))
            self.assertTrue(os.path.lexists("blah"))
            self.assertEqual("1", open("blah").read())
        finally:
            self.safeRemove("blah.chkpt")
            self.safeRemove("blah")


    def test_initWork(self):
        # Ensure that work added from @init works
        r = self.executePy("""
import job_stream.inline as inline
w = inline.Work([1])
@w.init
def addTwo():
    return 2
@w.init
def addThreeFour():
    return inline.Multiple([3, 4])
for r in w.run():
    print r
""")
        self.assertLinesEqual("1\n2\n3\n4\n", r[0])


    def test_jobAfterReduce(self):
        # Ensure that a global reducer allows a job afterwards for further processing and
        # branching
        r = self.executePy("""
import job_stream.inline as inline
work = inline.Work([ 1, 2, 3 ])

class SumStore(object):
    def __init__(self):
        self.value = 0
@work.reduce(store = SumStore, emit = lambda store: store.value)
def sum(store, work, others):
    for w in work:
        store.value += w
    for o in others:
        store.value += o.value

@work.job
def timesThree(work):
    return inline.Multiple([ work ] * 3)

work.reduce(sum, store = SumStore, emit = lambda store: store.value)
for r in work.run():
    print r
""")
        self.assertEqual("18\n", r[0])


    def test_listDirLineStats(self):
        # A complicated example, used to test the theory...
        r = self.executePy("""
import job_stream.inline as inline

import os

work = inline.Work(os.listdir('.'))
@work.job
def lineAvg(fname):
    avg = 0.0
    cnt = 0
    if not os.path.isfile(fname):
        return
    for l in open(fname):
        avg += len(l.rstrip())
        cnt += 1
    return (fname, cnt, avg / cnt)

@work.reduce(store = lambda: inline.Object(gavg = 0.0, gcnt = 0),
        emit = lambda store: (store.gcnt, store.gavg / max(1, store.gcnt)))
def findGlobalAvg(store, inputs, others):
    for i in inputs:
        store.gcnt += i[1]
        store.gavg += i[2] * i[1]
    for o in others:
        store.gcnt += o.gcnt
        store.gavg += o.gavg

for r in work.run():
    print repr(r)
""")

        r2 = self.executePy("""
import job_stream
import os

class AvgLines(job_stream.Job):
    def handleWork(self, w):
        avg = 0.0
        cnt = 0
        if not os.path.isfile(w):
            return
        for l in open(w):
            avg += len(l.rstrip())
            cnt += 1
        self.emit(( w, cnt, avg / max(1, cnt) ))


class FindGlobal(job_stream.Reducer):
    def handleInit(self, store):
        store.gavg = 0.0
        store.gcnt = 0
    def handleAdd(self, store, w):
        store.gavg += w[2] * w[1]
        store.gcnt += w[1]
    def handleJoin(self, store, other):
        store.gavg += other.gavg
        store.gcnt += other.gcnt
    def handleDone(self, store):
        self.emit(( store.gcnt, store.gavg / max(1, store.gcnt) ))

job_stream.work = os.listdir('.')
job_stream.run({
    'reducer': FindGlobal,
    'jobs': [ AvgLines ],
})
""")

        self.assertLinesEqual(r2[0], r[0])


    def test_result(self):
        # Ensure that result works as needed, and fails if it isn't last
        r = self.executePy("""
from job_stream.inline import Work
w = Work([ 1, 2, 3 ])
@w.result
def f(w):
    print("F: {}".format(w))
w.run()""")
        self.assertLinesEqual("F: 1\nF: 2\nF: 3\n", r[0])

        # Fail if defined twice
        with self.assertRaises(ExecuteError):
            self.executePy("""
from job_stream.inline import Work
w = Work()
@w.result
def j(w):
    pass
@w.result
def f(w):
    pass
""")

        # Fail if job defined after
        with self.assertRaises(ExecuteError):
            self.executePy("""
from job_stream.inline import Work
w = Work()
@w.result
def j(w):
    pass
@w.job
def f(w):
    pass
""")

        # Fail if frame defined after
        with self.assertRaises(ExecuteError):
            self.executePy("""
from job_stream.inline import Work
w = Work()
@w.result
def j(w):
    pass
@w.frame
def f(s, w):
    pass
@w.frameEnd
def ff(s, n):
    pass
""")

        # Fail if reducer defined after
        with self.assertRaises(ExecuteError):
            self.executePy("""
from job_stream.inline import Work
w = Work()
@w.result
def j(w):
    pass
@w.reduce
def f(s, w, o):
    pass
""")

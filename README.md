Job Stream
==========

A tiny C library based on OpenMPI for distributing streamed batch processing
across multi-threaded workers.


Requirements
------------

boost (mpi, serialization, thread)
yaml-cpp


Building
--------

Create a build/ folder, cd into it, and run:

    cmake .. && make -j8

This will instruct you on how to configure the build environment, and then will
build the library.


Testing
-------

Making the "test" target (with optional ARGS passed to test executable) will
make and run any tests packaged with job_stream:

    cmake .. && make -j8 test [ARGS="[serialization]"]


Running
-------

A typical job_stream application would be run like this:

    mpirun -host a,b,c my_application path/to/config.yaml [-c checkpointFile] [-t hoursBetweenCheckpoints] Initial work string (or int or float or whatever)

Note that -np to specify parallelism is not needed, as job_stream implicitly
multi-threads your application.  If a checkpointFile is provided, then the file
will be used if it exists.  If it does not exist, it will be created and updated
periodically to allow resume.  It is fairly simple to write a script that will
execute the application until success:

    RESULT=1
    for i in `seq 1 100`; do
        mpirun my_application config.yaml -c checkpoint.chkpt blahblah
        RESULT=$?
        if [ $RESULT -eq 0 ]; then
            break
        fi
    done

    exit $RESULT

If -t is not specified, checkpoints will be taken every 10 minutes.  Sometimes
checkpointing is a very slow process though; -t 24 will only checkpoint once
per day, for instance.


Running in Python
-----------------

Like this:

    LD_LIBRARY_PATH=/path/to/boost/libs YAML_CPP=/path/to/yaml-cpp [MPICXX=mpicxx] python setup.py install
    python
    >>> import job_stream
    >>> job_stream.run()

Other notes:
* Had to change yaml-cpp's CMake to include SHARED keyword to add_library(yaml-cpp SHARED.
* I sym-linked an awful lot of mpi libraries into yaml-cpp's build dir.  Problem was .0 extension added? (libmpi.so.0
  instead of libmpi.so)


Basics
------

job_stream works by allowing you to specify various "streams" through your
application's logic.  The most basic unit of work in job_stream is the job,
which takes some input work and transforms it into zero or more outputs:

![A job_stream job takes some input, transforms it, and emits zero or more outputs](doc/readme/01_basic.png)

That is, some input work is required for a job to do anything.  However, the
job may choose to not pass anything forward (perhaps save something to a file
instead), or it might apply some transformation(s) to the input and then output
the changed data.  For our first job, supppose we wanted to make a basic job
that takes an integer and increments it, forwarding on the result:

![A job that adds one to the input and emits it](doc/readme/02_addOne.png)

The corresponding code for this job follows:

    #include <job_stream/job_stream.h>
    //All work comes into job_stream jobs as a unique_ptr; this can be used
    //to optimize memory bandwidth locally.
    using std::unique_ptr;

    /** Add one to the integer input and forward it. */
    class AddOneJob : public job_stream::Job<AddOneJob, int> {
    public:
        /** The name used to describe this job in a YAML file */
        static const char* NAME() { return "addOne"; }
        void handleWork(unique_ptr<int> work) {
            this->emit(*work + 1);
        }
    } addOneJob;

The parts of note are:

* Template arguments to job_stream::Job - the class being defined, and the
  expected type of input,
* NAME() method, which returns a string that we'll use to refer to this
  type of job,
* handleWork() method, which is called for each input work generated,
* this->emit() call, which is used to pass some serializable object forward as
  output, and
* this->emit() can take any type of argument - the output's type and content do
  not need to have any relation to the input.
* There MUST be a global instance allocated after the class definition.  This
  instance is not ever used in code, but C++ requires a instance for certain
  templated code to be generated.

*NOTE - all methods in a job_stream job must be thread-safe!*

In order to use this job, we would need to define a simple `adder.yaml` file:

    jobs:
      - type: addOne

Running this with some input produces the expected result:

    local$ pwd
    /.../dev/job_stream
    local$ cd build
    local$ cmake .. && make -j8 example
    ...
    # Any arguments after the YAML file and any flags mean to run the job stream
    # with precisely one input, interpreted from the arguments
    local$ example/job_stream_example ../example/adder.yaml 1
    2
    (some stats will be printed on termination)
    # If no arguments exist, then stdin will be used.
    local$ example/job_stream_example ../example/adder.yaml <<!
    3
    8
    !
    # Results - note that when you run this, the 9 might print before the 4!
    # This depends on how the thread scheduling works out.
    4
    9
    (some stats will be printed on termination)
    local$

Reducers and Frames
-------------------
Of course, if we could only transform and potentially duplicate input then
job_stream wouldn't be very powerful.  job_stream has two mechanisms that make
it much more useful - reducers, which allow several independently processed
work streams to be merged, and recursion, which allows a reducer to pass work
back into itself.  Frames are a job_stream idiom to make the combination of
reducers and recursion more natural.

To see how this fits, we'll calculate pi experimentally to a desired precision.
We'll be using the area calculation - since A = R*pi^2, pi = sqrt(A / R).  
Randomly distributing points in a 1x1 grid and testing if they lie within the
unit circle, we can estimate the area:

![Estimating pi](doc/readme/03_calculatePi.png)

The job_stream part of this will take as its input a floating point number which
is the percentage of error that we want to reach, and will emit the number of
experimental points evaluated in order to reach that accuracy.  The network
looks like this:

![Estimating pi](doc/readme/04_piPipeline.png)

As an aside, the "literally anything" that the piCalculator needs to feed to
piEstimate is because we'll have piEstimate decide which point to evaluate.  
This is an important part of designing a job_stream pipeline - generality.  If,
for instance, we were to pass the point that needs evaluating to piEstimate,
then we have locked our piCalculator into working with only one method of
evaluating pi.  With the architecture shown, we can substitute any number of
pi estimators and compare their relative efficiencies.

Before coding our jobs, let's set up the YAML file `pi.yaml`:

    jobs:
      - frame:
            type: piCalculator
        jobs:
            - type: piEstimate

This means that our pipe will consist of one top-level job, which itself has no
type and a stream of "jobs" it will use to transform data.  Wrapped around its
stream is a "frame" of type piCalculator.  This corresponds to our above
diagram.

piCalculator being a frame means that it will take an initial work,
recur into itself, and then aggregate results (which may be of a different type
than the initial work) until it stops recurring.  The code for it looks like
this:

    struct PiCalculatorState {
        float precision;
        float piSum;
        int trials;

    private:
        //All structures used for storage or emit()'d must be serializable
        friend class boost::serialization::access;
        template<class Archive>
        void serialize(Archive& ar, const unsigned int version) {
            ar & precision & piSum & trials;
        }
    };

    /** Calculates pi to the precision passed as the first work.  The template
        arguments for a Frame are: the Frame's class, the storage type, the
        first work's type, and subsequent (recurred) work's type. */
    class PiCalculator : public job_stream::Frame<PiCalculator,
            PiCalculatorState, float, float> {
    public:
        static const char* NAME() { return "piCalculator"; }

        void handleFirst(PiCalculatorState& current, unique_ptr<float> work) {
            current.precision = *work * 0.01;
            current.piSum = 0.0f;
            current.trials = 0;
            //Put work back into this Frame.  This will trigger whatever method
            //of pi approximation is defined in our YAML.  We'll pass the
            //current trial index as debug information.
            this->recur(current.trials++);
        }

        void handleWork(PiCalculatorState& current, unique_ptr<float> work) {
            current.piSum += *work;
        }

        void handleDone(PiCalculatorState& current) {
            //Are we done?
            float piCurrent = current.piSum / current.trials;
            if (fabsf((piCurrent - M_PI) / M_PI) < current.precision) {
                //We're within desired precision, emit trials count
                fprintf(stderr, "Pi found to be %f, +- %.1f%%\n", piCurrent,
                        current.precision * 100.f);
                this->emit(current.trials);
            }
            else {
                //We need more iterations.  Double our trial count
                for (int i = 0, m = current.trials; i < m; i++) {
                    this->recur(current.trials++);
                }
            }
        }
    } piCalculator;

Similar to our first addOne job, but we've added a few extra methods -
handleFirst and handleDone.  handleFirst is called for the work that starts
a reduction and should initialize the state of the current reduction.  
handleWork is called whenever a recur'd work finishes its loop and ends up back
at the Frame.  Its result should be integrated into the current state somehow.
handleDone is called when there is no more pending work in the frame, at which
point the frame may either emit its current result or recur more work.  If
nothing is recur'd, the reduction is terminated.

Our piEstimate job is much simpler:

    class PiEstimate : public job_stream::Job<PiEstimate, int> {
    public:
        static const char* NAME() { return "piEstimate"; }
        void handleWork(unique_ptr<int> work) {
            float x = rand() / (float)RAND_MAX;
            float y = rand() / (float)RAND_MAX;
            if (x * x + y * y <= 1.0) {
                //Estimate area as full circle
                this->emit(4.0f);
            }
            else {
                //Estimate area as nothing
                this->emit(0.0f);
            }
        }
    } piEstimate;

So, let's try it!

    local$ cd build
    local$ cmake .. && make -j8 example
    local$ example/job_stream_example ../example/pi.yaml 10
    Pi found to be 3.000000, +- 10.0%
    4
    (debug info as well)

So, it took 4 samples to arrive at a pi estimation of 3.00, which is within 10%
of 3.14.  Hooray!  We can also run several tests concurrently:

    local$ example/job_stream_exmaple ../example/pi.yaml <<!
    10
    1
    0.1
    !
    Pi found to be 3.000000, +- 10.0%
    4
    Pi found to be 3.167969, +- 1.0%
    Pi found to be 3.140625, +- 0.1%
    1024
    1024
    0 4% user time (3% mpi), 1% user cpu, 977 messages (0% user)
    C 4% user time, 0% user cpu, quality 0.00 cpus, ran 1.238s

The example works!  Bear in mind that the efficiency ratings for a task like
this are pretty poor.  Since each job only does a few floating point operations,
he communication overhead well outweighs the potential benefits of parallelism.
However, once your jobs start to do even a little more work, job_stream quickly
becomes beneficial.  On our modest research cluster, I have jobs that routinely
report a user-code quality of 200+ cpus.


Words of Warning
----------------

fork()ing a child process can be difficult in a threaded MPI application.  To
work around these difficulties, it is suggested that your application use
job_stream::invoke (which forwards commands to a properly controlled
libexecstream).

Job and reduction routines MUST be thread safe.  Job_stream handles most of this
for you.  However, do NOT create a shared buffer in which to do your work as
part of a job class.  If you do, make sure you declare it thread\_local (which
requires static).

If you use checkpoints and your process crashes, it is possible that any
activity _outside_ of job_stream will be repeated.  In other words, if one of
your jobs appends content to a file, then that content might appear in the
file multiple times.  The recommended way to get around this is to have your
work output to different files, with a unique, deterministic file name for each
piece of work that outputs.  Another approach is to use a reducer which gathers
all completed work, and then dumps it all to a file at once in handleDone().

Sometimes, passing -bind-to-core to mpirun can have a profoundly positive impact
on performance.


Unfriendly Examples
-------------------

*These are old and aren't laid out quite as nicely.  However, there is reasonably
good information here that isn't covered above.  So, it's left here for now.*

The following example is fully configured in the "example" subdirectory.

Essentially, you code some jobs, and optionally a reducer for combining results:

    #include <job_stream/job_stream.h>

    using std::unique_ptr;

    /** Add one to any integer we receive */
    class AddOneJob : public job_stream::Job<int> {
    public:
        static AddOneJob* make() { return new AddOneJob(); }

        void handleWork(unique_ptr<int> work) {
            this->emit(*work + 1);
        }
    };


    class DuplicateJob : public job_stream::Job<int> {
    public:
        static DuplicateJob* make() { return new DuplicateJob(); }

        void handleWork(unique_ptr<int> work) {
            this->emit(*work);
            this->emit(*work);
        }
    };


    class GetToTenJob : public job_stream::Job<int> {
    public:
        static GetToTenJob* make() { return new GetToTenJob(); }

        void handleWork(unique_ptr<int> work) {
            if (*work < 10) {
                this->emit(*work, "keep_going");
            }
            else {
                this->emit(*work, "done");
            }
        }
    };


    class SumReducer : public job_stream::Reducer<int> {
    public:
        static SumReducer* make() { return new SumReducer(); }

        /** Called to initialize the accumulator for this reduce.  May be called
            several times on different hosts, whose results will later be merged
            in handleJoin(). */
        void handleInit(int& current) {
            current = 0;
        }

        /** Used to add a new output to this Reducer */
        void handleAdd(int& current, unique_ptr<int> work) {
            current += *work;
        }

        /** Called to join this Reducer with the accumulator from another */
        void handleJoin(int& current, unique_ptr<int> other) {
            current += *other;
        }

        /** Called when the reduction is complete, or nearly - recur() may be used
            to keep the reduction alive (inject new work into this reduction). */
        void handleDone(int& current) {
            this->emit(current);
        }
    };


    class GetToValueReducer : public job_stream::Reducer<int> {
    public:
        static GetToValueReducer* make() { return new GetToValueReducer(); }

        void handleInit(int& current) {
            current = 0;
        }

        void handleAdd(int& current, unique_ptr<int> work) {
            //Everytime we get an output less than 2, we'll need to run it through
            //the system again.
            printf("Adding %i\n", *work);
            if (*work < 3) {
                this->recur(3);
            }
            current += *work;
        }

        void handleJoin(int& current, unique_ptr<int> other) {
            current += *other;
        }

        void handleDone(int& current) {
            printf("Maybe done at %i\n", current);
            if (current >= this->config["value"].as<int>()) {
                this->emit(current);
            }
            else {
                //Not really done, put work back in as our accumulated value.
                this->recur(current);
            }
        }
    };

Register them in your main, and call up a processor:

    int main(int argc, char* argv []) {
        job_stream::addJob("addOne", AddOneJob::make);
        job_stream::addJob("duplicate", DuplicateJob::make);
        job_stream::addJob("getToTen", GetToTenJob::make);
        job_stream::addReducer("sum", SumReducer::make);
        job_stream::addReducer("getToValue", GetToValueReducer::make);
        job_stream::runProcessor(argc, argv);
        return 0;
    }

Define a pipeline / configuration:

    # example1.yaml
    reducer: sum
    jobs:
        - type: addOne
        - type: addOne

And run it!

    # This will compute 45 + 2 and 7 + 2 separately, then sum them, returning
    # one number (because of the reducer).
    $ mpirun -np 4 ./job_stream_example example1.yaml <<!
        45
        7
        !
    56
    $

Want to get a little more complicated?  You can embed modules:

    # example2.yaml
    jobs:
        - type: addOne
        # Not defining type (or setting it to "module") starts a new module
        # that can have its own reducer and job chain
        -   reducer: sum
            jobs:
                - type: duplicate

That pipeline will, individually for each input row, add one and double it:

    $ mpirun -np 4 ./job_stream_example example2.yaml <<!
        1
        2
        3
        !
    4
    6
    8
    $

Does your program have more complex flow?  The emit() function can take a second
argument, which is the name of the target to route to.  For instance, if we add
to main.cpp:

    class GetToTenJob : public job_stream::Job<int> {
    public:
        static GetToTenJob* make() { return new GetToTenJob(); }

        void handleWork(unique_ptr<int> work) {
            if (*work < 10) {
                this->emit(*work, "keep_going");
            }
            else {
                this->emit(*work, "done");
            }
        }
    };

    //Remember to register it in main...

And then you set up example3.yaml:

    # example3.yaml
    # Note that our module now has an "input" field - this determines the first
    # job to receive work.  Our "jobs" field is now a map instead of a list,
    # with the key being the id of each job.  "to" determines where emitted
    # work goes - if "to" is a mapping, the job uses "emit" with a second
    # argument to guide each emitted work.
    input: checkValue
    jobs:
        addOne:
            type: addOne
            to: checkValue
        checkValue:
            type: getToTen
            to:
                keep_going: addOne
                done: output

Run it:

    $ mpirun -np 4  ./job_stream_example example3.yaml <<!
        1
        8
        12
        !
    12
    10
    10
    $

Note that the "12" is output first, since it got routed to output almost
immediately rather than having to pass through many AddOneJobs.

You can also have recurrence in your reducers - that is, if a reduction finishes
but the results do not match a criteria yet, you can put more tuples through
in the same reduction:

    # example4.yaml
    # Reducer recurrence
    reducer:
        type: getToValue
        value: 100
    jobs:
        - type: duplicate
        - type: addOne

Running this with 1 will yield 188 - essentially, since handleAdd() calls recur
for each value less than 3, two additional "3" works get added into the system
early on.  So handleDone() gets called with 20, 62, and finally 188.



Recent Changelog
----------------
* 2014-12-18 - Python support.  Frame methods renamed for clarity
  (handleWork -> handleNext).
* 2014-12-04 - Checkpoints no longer are allowed for interactive mode.  All
  input must be spooled into the system before a checkpoint will be allowed.
* 2014-11-14 - Fixed job_stream checkpoints to be continuous.  That is, a
  checkpoint no longer needs current work to finish in order to complete.  This
  cuts the runtime for checkpoints from several hours in some situations down
  to a couple of seconds.  Also, added test-long to cmake, so that tests can
  be run repeatedly for any period of time in order to track down transient
  failures.

  Fixed a bug with job_stream::invoke which would lock up if a program wrote
  too much information to stderr or stdout.

  Re-did steal ring so that it takes available processing power into account.
* 2014-11-06 - Fixed invoke::run up so that it supported retry on user-defined
  transient errors (For me, Xyce was having issues creating a sub directory
  and would crash).
* 2014-11-03 - Added --checkpoint-info for identifying what makes checkpoint
  files so large sometimes.  Miscellaneous cleanup to --help functionality.
  Serialization will refuse to serialize a non-pointer version of a polymorphic
  class, since it takes a long time to track down what's wrong in that
  situation.
* 2014-10-17 - Apparently yaml-cpp is not thread safe.  Wtf.  Anyway, as a
  "temporary" solution, job_stream now uses some custom globally locked classes
  as a gateway to yaml-cpp.  All functionality should still work exactly like
  vanilla yaml-cpp.

  Also, no work happens during a checkpoint now.  That was causing corrupted
  checkpoint files with duplicated ring tests.
* 2014-9-10 - Fixed up duplicated and end-of-job-sequence (output) submodules.
  Host name is now used in addition to MPI rank when reporting results.
* 2014-6-13 - Finalized checkpoint code for initial release.  A slew of new
  tests.  
* 2014-4-24 - Fixed up shared_ptr serialization.  Fixed synchronization issue
  in reduction rings.
* 2014-2-19 - Added Frame specialization of Reducer.  Expects a different
  first work than subsequent.  Usage pattern is to do some initialization work
  and then recur() additional work as needed.
* 2014-2-12 - Serialization is now via pointer, and supports polymorphic classes
  completely unambiguously via dynamic_cast and
  job_stream::serialization::registerType.  User cpu % updated to be in terms of
  user time (quality measure) for each processor, and cumulative CPUs for
  cumulative time.  
* 2014-2-5 - In terms of user ticks / wall clock ms, less_serialization is on
  par with master (3416 vs 3393 ticks / ms, 5% error), in addition
  to all of the other fixes that branch has.  Merged in.
* 2014-2-4 - Got rid of needed istream specialization; use an if and a
  runtime\_exception.
* 2014-2-4 - handleWork, handleAdd, and handleJoin all changed to take a
  unique\_ptr rather than references.  This allows preventing more memory
  allocations and copies.  Default implementation with += removed.


Roadmap
-------

* README update - Should mention symlinking.
* to: Should be a name or YAML reference, emit() or recur() should accept an
  argument of const YAML::Node& so that we can use e.g. stepTo: *priorRef as
  a normal config.  DO NOT overwrite to!  Allow it to be specified in pipes, e.g.

    - to: *other
      needsMoreTo: *next
    - &next
      type: ...
      to: output
    - &other
      type: ...

  In general, allow standard YAML rather than a specially split "to" member.
* Smarter serialization....... maybe hash serialized entities, and store a dict
  of hashes, so as to only write the same data once even if it is NOT a
  duplicated pointer.
* depth-first iteration as flag
* Ability to let job_stream optimize work size.  That is, your program says
  something like this->getChunk(__FILE__, __LINE__, 500) and then job_stream
  tracks time spent on communicating vs processing and optimizes the size of
  the work a bit...
* Fix timing statistics in continue'd runs from checkpoints
* Errors during a task should push the work back on the stack and trigger a
  checkpoint before exiting.  That would be awesome.  Should probably be an
  option though, since it would require "checkpointing" reduce accumulations
  and holding onto emitted data throughout each work's processing
* Prevent running code on very slow systems... maybe make a CPU / RAM sat
  metric by running a 1-2 second test and see how many cycles of computation
  we get, then compare across systems.  If we also share how many contexts each
  machine has, then stealing code can balance such that machines 1/2 as capable
  only keep half their cores busy maximum according to stealing.
* Progress indicator, if possible...
* Merge job\_stream\_inherit into job\_stream\_example (and test it)
* TIME\_COMM should not include initial isend request, since we're not using
  primitive objects and that groups in the serialization time
* Frame probably shouldn't need handleJoin (behavior would be wrong, since
  the first tuple would be different in each incarnation)
* Replace to: output with to: parent; input: output to input: reducer
* Consider replacing "reducer" keyword with "frame" to automatically rewrite
  recurTo as input and input as reducer
* Consider attachToNext() paired w/ emit and recur; attachments have their own
  getAttached<type>("label") retriever that returns a modifiable version of the
  attachment.  removeAttached("label").  Anyway, attachments go to all child
  reducers but are not transmitted via emitted() work from reducers.  Would
  greatly simplify trainer / maximize code... though, if something is required,
  passing it in a struct is probably a better idea as it's a compile-time error.
  Then again, it wouldn't work for return values, but it would work for
  attaching return values to a recur'd tuple and waiting for it to come back
  around.
* Update README with serialization changes, clean up code.  Note that unique\_ptr
  serialize() is specified in serialization.h.  Also Frame needs doc.
* Idle time tracking - show how much time is spent e.g. waiting on a reducer
* Solve config problem - if e.g. all jobs need to fill in some globally shared
  information (tests to run, something not in YAML)
* Python embedded bindings / application
* Reductions should always happen locally; a dead ring should merge them.  
    * Issue - would need a merge() function on the templated reducer base class.  Also, recurrence would have to re-initialize those rings.  Might be better to hold off on this one until it's a proven performance issue.
    * Unless, of course, T_accum == T_input always and I remove the second param.  Downsides include awkwardness if you want other components to feed into the reducer in a non-reduced format... but, you'd have to write a converter anyway (current handleMore).  So...
    * Though, if T_accum == T_input, it's much more awkward to make generic, modular components.  For instance, suppose you have a vector calculation.  Sometimes you just want to print the vectors, or route them to a splicer or whatever.  If you have to form them as reductions, that's pretty forced...
    * Note - decided to go with handleJoin(), which isn't used currently, but will be soon (I think this will become a small issue)
* Tests
* Subproject - executable integrated with python, for compile-less / easier work

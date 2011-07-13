#!/usr/bin/env python
# Copyright 2011 GRNET S.A. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#   1. Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#
#  2. Redistributions in binary form must reproduce the above copyright
#     notice, this list of conditions and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE REGENTS AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE REGENTS OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.
#
# The views and conclusions contained in the software and documentation are
# those of the authors and should not be interpreted as representing official
# policies, either expressed or implied, of GRNET S.A.


""" Message queue setup, dispatch and admin

This program sets up connections to the queues configured in settings.py
and implements the message wait and dispatch loops. Actual messages are
handled in the dispatched functions.

"""
from django.core.management import setup_environ

import sys
import os
path = os.path.normpath(os.path.join(os.getcwd(), '..'))
sys.path.append(path)
import synnefo.settings as settings
from synnefo.logic import log

setup_environ(settings)

from amqplib import client_0_8 as amqp
from signal import signal, SIGINT, SIGTERM

import time
import socket
from daemon import daemon

# Take care of differences between python-daemon versions.
try:
    from daemon import pidfile
except:
    from daemon import pidlockfile

from synnefo.logic import callbacks

class Dispatcher:

    logger = None
    chan = None
    debug = False
    clienttags = []

    def __init__(self, debug = False):
        
        # Initialize logger
        self.logger = log.get_logger('synnefo.dispatcher')

        self.debug = debug
        self._init()

    def wait(self):
        while True:
            try:
                self.chan.wait()
            except SystemExit:
                break
            except amqp.exceptions.AMQPConnectionException:
                self.logger.error("Server went away, reconnecting...")
                self._init()
            except socket.error:
                self.logger.error("Server went away, reconnecting...")
                self._init()

        [self.chan.basic_cancel(clienttag) for clienttag in self.clienttags]
        self.chan.connection.close()
        self.chan.close()

    def _init(self):
        self.logger.info("Initializing")
        
        # Connect to RabbitMQ
        conn = None
        while conn == None:
            self.logger.info("Attempting to connect to %s",
                             settings.RABBIT_HOST)
            try:
                conn = amqp.Connection(host=settings.RABBIT_HOST,
                                       userid=settings.RABBIT_USERNAME,
                                       password=settings.RABBIT_PASSWORD,
                                       virtual_host=settings.RABBIT_VHOST)
            except socket.error:
                time.sleep(1)

        self.logger.info("Connection succesful, opening channel")
        self.chan = conn.channel()

        # Declare queues and exchanges
        for exchange in settings.EXCHANGES:
            self.chan.exchange_declare(exchange=exchange, type="topic",
                                       durable=True, auto_delete=False)

        for queue in settings.QUEUES:
            self.chan.queue_declare(queue=queue, durable=True,
                                    exclusive=False, auto_delete=False)

        bindings = settings.BINDINGS

        # Special queue for debugging, should not appear in production
        if self.debug and settings.DEBUG:
            self.chan.queue_declare(queue=settings.QUEUE_DEBUG, durable=True,
                                    exclusive=False, auto_delete=False)
            bindings += settings.BINDINGS_DEBUG

        # Bind queues to handler methods
        for binding in bindings:
            try:
                callback = getattr(callbacks, binding[3])
            except AttributeError:
                self.logger.error("Cannot find callback %s" % binding[3])
                continue

            self.chan.queue_bind(queue=binding[0], exchange=binding[1],
                                 routing_key=binding[2])
            tag = self.chan.basic_consume(queue=binding[0], callback=callback)
            self.logger.debug("Binding %s(%s) to queue %s with handler %s" %
                              (binding[1], binding[2], binding[0], binding[3]))
            self.clienttags.append(tag)


def _exit_handler(signum, frame):
    """"Catch exit signal in children processes."""
    print "%d: Caught signal %d, will raise SystemExit" % (os.getpid(), signum)
    raise SystemExit


def _parent_handler(signum, frame):
    """"Catch exit signal in parent process and forward it to children."""
    global children
    print "Caught signal %d, sending kill signal to children" % signum
    [os.kill(pid, SIGTERM) for pid in children]


def child(cmdline):
    """The context of the child process"""

    # Cmd line argument parsing
    (opts, args) = parse_arguments(cmdline)
    disp = Dispatcher(debug = opts.debug)

    # Start the event loop
    disp.wait()


def parse_arguments(args):
    from optparse import OptionParser

    parser = OptionParser()
    parser.add_option("-d", "--debug", action="store_true", default=False,
                      dest="debug", help="Enable debug mode")
    parser.add_option("-w", "--workers", default=2, dest="workers",
                      help="Number of workers to spawn", type="int")
    parser.add_option("-p", '--pid-file', dest="pid_file",
                      default=os.path.join(os.getcwd(), "dispatcher.pid"),
                      help="Save PID to file (default:%s)" %
                           os.path.join(os.getcwd(), "dispatcher.pid"))
    parser.add_option("--purge-queues", action="store_true",
                      default=False, dest="purge_queues",
                      help="Remove all declared queues (DANGEROUS!)")
    parser.add_option("--purge-exchanges", action="store_true",
                      default=False, dest="purge_exchanges",
                      help="Remove all exchanges. Implies deleting all queues \
                           first (DANGEROUS!)")
    parser.add_option("--drain-queue", dest="drain_queue",
                      help="Strips a queue from all outstanding messages")

    return parser.parse_args(args)


def purge_queues() :
    """
        Delete declared queues from RabbitMQ. Use with care!
    """
    conn = get_connection()
    chan = conn.channel()

    print "Queues to be deleted: ",  settings.QUEUES

    if not get_user_confirmation():
        return

    for queue in settings.QUEUES:
        try:
            chan.queue_delete(queue=queue)
            print "Deleting queue %s" % queue
        except amqp.exceptions.AMQPChannelException as e:
            print e.amqp_reply_code, " ", e.amqp_reply_text
            chan = conn.channel()

    chan.connection.close()


def purge_exchanges():
    """
        Delete declared exchanges from RabbitMQ, after removing all queues first
    """
    purge_queues()

    conn = get_connection()
    chan = conn.channel()

    print "Exchnages to be deleted: ", settings.EXCHANGES

    if not get_user_confirmation():
        return

    for exchange in settings.EXCHANGES:
        try:
            chan.exchange_delete(exchange=exchange)
        except amqp.exceptions.AMQPChannelException as e:
            print e.amqp_reply_code, " ", e.amqp_reply_text

    chan.connection.close()


def drain_queue(queue):
    """
        Strip a (declared) queue from all outstanding messages
    """
    if not queue:
        return

    if not queue in settings.QUEUES:
        print "Queue %s not configured" % queue
        return

    print "Queue to be drained: %s" % queue

    if not get_user_confirmation():
        return
    conn = get_connection()
    chan = conn.channel()

    # Register a temporary queue binding
    for binding in settings.BINDINGS:
        if binding[0] == queue:
            exch = binding[1]

    if not exch:
        print "Queue not bound to any exchange: %s" % queue
        return

    chan.queue_bind(queue=queue, exchange=exch,routing_key='#')
    tag = chan.basic_consume(queue=queue, callback=callbacks.dummy_proc)

    print "Queue draining about to start, hit Ctrl+c when done"
    time.sleep(2)
    print "Queue draining starting"

    signal(SIGTERM, _exit_handler)
    signal(SIGINT, _exit_handler)

    num_processed = 0
    while True:
        chan.wait()
        num_processed += 1
        sys.stderr.write("Ignored %d messages\r" % num_processed)

    chan.basic_cancel(tag)
    chan.connection.close()


def get_connection():
    conn = amqp.Connection( host=settings.RABBIT_HOST,
                        userid=settings.RABBIT_USERNAME,
                        password=settings.RABBIT_PASSWORD,
                        virtual_host=settings.RABBIT_VHOST)
    return conn


def get_user_confirmation():
    ans = raw_input("Are you sure (N/y):")

    if not ans:
        return False
    if ans not in ['Y', 'y']:
        return False
    return True


def debug_mode():
    disp = Dispatcher(debug = True)
    signal(SIGINT, _exit_handler)
    signal(SIGTERM, _exit_handler)

    disp.wait()


def main():
    global children, logger
    (opts, args) = parse_arguments(sys.argv[1:])

    logger = log.get_logger("synnefo.dispatcher")

    # Special case for the clean up queues action
    if opts.purge_queues:
        purge_queues()
        return

    # Special case for the clean up exch action
    if opts.purge_exchanges:
        purge_exchanges()
        return

    if opts.drain_queue:
        drain_queue(opts.drain_queue)
        return

    # Debug mode, process messages without spawning workers
    if opts.debug:
        debug_mode()
        return

    # Become a daemon
    daemon_context = daemon.DaemonContext(
        stdout=sys.stdout,
        stderr=sys.stderr,
        umask=022)

    daemon_context.open()

    # Create pidfile. Take care of differences between python-daemon versions.
    try:
        pidf = pidfile.TimeoutPIDLockFile(opts.pid_file, 10)
    except:
        pidf = pidlockfile.TimeoutPIDLockFile(opts.pid_file, 10)

    pidf.acquire()

    logger.info("Became a daemon")

    # Fork workers
    children = []

    i = 0
    while i < opts.workers:
        newpid = os.fork()

        if newpid == 0:
            signal(SIGINT,  _exit_handler)
            signal(SIGTERM, _exit_handler)
            child(sys.argv[1:])
            sys.exit(1)
        else:
            pids = (os.getpid(), newpid)
            logger.debug("%d, forked child: %d" % pids)
            children.append(pids[1])
        i += 1

    # Catch signals to ensure graceful shutdown
    signal(SIGINT,  _parent_handler)
    signal(SIGTERM, _parent_handler)

    # Wait for all children processes to die, one by one
    try :
        for pid in children:
            try:
                os.waitpid(pid, 0)
            except Exception:
                pass
    finally:
        pidf.release()

if __name__ == "__main__":
    sys.exit(main())

# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :

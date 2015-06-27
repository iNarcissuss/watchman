#! /usr/bin/env python
# -*- coding: utf-8 -*-


__author__ = "YohKmb <yoh134shonan@gmail.com>"
__status__ = "development"
__version__ = "0.0.1"
__date__    = "July 2015"


import socket, struct, array
import sys, os, time, logging
import argparse

from threading import Thread, Event, current_thread
from collections import defaultdict, namedtuple
from functools import wraps
from contextlib import closing
# from multiprocessing import Process


# choose an apppropriate timer module depending on the platform
if sys.platform == "win32":
    default_timer = time.clock
else:
    default_timer = time.time


class ICMP():

    ICMP_ECHO_REQ = 0x08
    ICMP_ECHO_REP = 0x00

    PROTO_STRUCT_FMT = "!BBHHH"
    PROTO_CODE = socket.getprotobyname("icmp")

    NBYTES_TIME = struct.calcsize("d")

    def _checksum_wrapper(func):

        if sys.byteorder == "big":

            @wraps(func)
            def _checksum_inner(cls, pkt):
                if len(pkt) % 2 == 1:
                    pkt += "\0"
                s = sum(array.array("H", pkt))
                s = (s >> 16) + (s & 0xffff)
                s += s >> 16
                s = ~s
                return s & 0xffff

        else:
            @wraps(func)
            def _checksum_inner(cls, pkt):
                if len(pkt) % 2 == 1:
                    pkt += "\0"
                s = sum(array.array("H", pkt))
                s = (s >> 16) + (s & 0xffff)
                s += s >> 16
                s = ~s
                return (((s>>8)&0xff)|s<<8) & 0xffff

        return _checksum_inner

    @classmethod
    @_checksum_wrapper
    def checksum(cls, pkt):
        pass


class ICMP_Request(ICMP):

    @classmethod
    def new_request(self, id, seq):

        # Temporary header whose checksum is set to 0
        chsum = 0

        # Tuple format := ("type", "code", "checksum", "id", "seq")
        req_header = struct.pack(ICMP_Request.PROTO_STRUCT_FMT,
                                 ICMP_Request.ICMP_ECHO_REQ, 0, chsum, id, seq)

        t_send = default_timer()

        data = (64 - ICMP.NBYTES_TIME) * "P"
        data = struct.pack("d", t_send) + data

        chsum = ICMP.checksum(req_header + data)
        req_header = struct.pack(ICMP.PROTO_STRUCT_FMT,
                                 ICMP.ICMP_ECHO_REQ, 0, chsum, id, seq)
        packet = req_header + data

        return packet


class ICMP_Reply(ICMP):

    @classmethod
    def decode_packet(self, packet, id_sent):

        header = packet[20:28]
        t_recv = default_timer()

        try:
            type, code, checksum, id_recv, seq = struct.unpack(ICMP.PROTO_STRUCT_FMT, header)
        except struct.error as excpt:
            logging.warning("invalid header was detected : {0}".format(str(header)))
            return None

        if type == ICMP.ICMP_ECHO_REP and id_recv == id_sent:
            t_send = struct.unpack("d", packet[28:28 + ICMP.NBYTES_TIME])[0]

            resp_time =  t_recv - t_send
            return (seq, resp_time)

        return None

class Result_Ping(namedtuple("Resut_Ping", ("addr", "seq", "resp_t"))):

    def __repr__(self):
        return "addr={0}, seq={1}, response_time={2}".format( *tuple(self))

    def as_record(self):
        rec = self._asdict()
        del(rec["addr"])

        return dict(rec)

# class Pinger(Process):
class Pinger(Thread):

    MAX_TARGET = 5
    INTERVAL_PING = 1.0
    LEN_RECV = 1024

    PROTO_STRUCT_FMT = "!BBHHH"
    # Result_Ping = namedtuple("Resut_Ping", ("addr", "seq", "resp_t"))

    def __init__(self, targets={}, timeout=3.0, is_receiver=False):

        super(Pinger, self).__init__()

        self._id = os.getpid() & 0xFFFF

        self._is_receiver = is_receiver
        self._timeout = timeout

        if is_receiver:
            self._results = defaultdict(lambda: [])
            self._work_on_myduty = self._recv
        else:
            self._targets = targets
            self._seqs = defaultdict(lambda: 1)
            self._work_on_myduty = self._send

        self.daemon = True
        self._ev = Event()

    def run(self):
        try:
            with closing(socket.socket(family=socket.AF_INET, type=socket.SOCK_RAW,
                                         proto=ICMP.PROTO_CODE) ) as sock:
                sock.settimeout(self._timeout)

                while True:
                    res = self._work_on_myduty(sock)
                    # if res:
                    #     print res

                    if res and self._is_receiver:
                        self._results[res.addr].append(res.as_record() )
                        print res

                    if self._ev.is_set():
                        logging.info(str(current_thread()) + " got a signal for ending")
                        break

                if self._is_receiver:
                    print self._results

        except socket.error as excpt:
            logging.error(excpt.__class__)
            sys.exit(1)

    def end(self):
        self._ev.set()

    @property
    def targets(self):
        return self._targets

    @targets.setter
    def targets(self, targets):
        self._targets = targets

    def _send(self, sock):
        results = []
        for target in self._targets.keys():
            res = self._send_one(sock, target)
            results.append(res)

        time.sleep(1.0)
        return results

    def _recv(self, sock):

        try:
            packet, addr_port = sock.recvfrom(self.LEN_RECV)

        except socket.timeout as excpt:
            logging.info("receive timeout occurred")
            packet = None

        if packet:
            seq, resp_time = ICMP_Reply.decode_packet(packet, self._id)
            addr, port = addr_port

            return Result_Ping(addr, seq, resp_time)
            # return self.Result_Ping(addr, seq, resp_time)

        else:
            return None


    def _send_one(self, sock, addr_dst):

        seq = self._seqs[addr_dst]
        packet = ICMP_Request.new_request(self._id, seq)
        self._seqs[addr_dst] += 1

        try:
            len_send = sock.sendto(packet, (addr_dst, 0))

        except socket.error as excpt:
            logging.error("failed to sending to {0}".format(addr_dst))
            raise excpt

        return len_send

    @classmethod
    def _slice_lists(cls, z, splited=[]):

        if len(z) > 5:
            splited.append(z[:5])
            return cls._slice_lists(z[5:], splited)

        else:
            splited.append(z)
            return splited

    @classmethod
    def _resolve_name(cls, targets):

        resolved = []
        removed = []
        for target in targets:
            print target
            try:
                addr_dst = socket.gethostbyname(target)
                # print str(target) + " = " + str(addr_dst)
                resolved.append(addr_dst)

            except socket.gaierror as excpt:
                logging.warning("fqdn {0} couldn't be resolved and is going to be ignored".format(target))
                removed.append(target)

        targets = [target for target in targets if target not in removed]
        sliced_tuples = cls._slice_lists(zip(resolved, targets))
        # sliced_tuples = cls._slice_lists(zip(targets, resolved))
        return [dict(tup) for tup in sliced_tuples]

    @classmethod
    def generate_senders(cls, targets):
        grps_target = cls._resolve_name(targets)

        senders = []
        for grp in grps_target:
            senders.append(cls(targets=grp) )

        return senders



def get_parser():

    desc = "pinger : an implementation of icmp utility with standard python libraries"
    parser = argparse.ArgumentParser(description=desc)

    parser.add_argument("targets", metavar="TARGET", type=str, nargs="*", help="targets to be pinged")
    parser.add_argument("-f", "--file", dest="file", type=str, required=False, help="filepath to the file that lists ping targets")

    return parser


def main():

    parser = get_parser()
    args = parser.parse_args()

    if args.file and args.targets:
        logging.error(" please specify target either commandline argument or filepath\n")
        parser.print_help()
        sys.exit(1)

    senders = Pinger.generate_senders(args.targets)
    receiver = Pinger(is_receiver=True)

    try:
        for sender in senders:
            sender.start()

        receiver.start()

        while True:
            raw_input()

    except KeyboardInterrupt as excpt:

        logging.info("Keyboard Interrupt occur. Program will exit.")

        for sender in senders:
            sender.end()

        receiver.end()

        for sender in senders:
            sender.join()

        receiver.join()

        sys.exit(0)

    logging.info("main program exits")


if __name__ == "__main__":
    main()

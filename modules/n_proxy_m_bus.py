"""Opens a proxy/function bus between 2 ports for a N-proxy-M PubSub pattern

  Additional info:
2 modes:
1. proxy (no data copying) - DEFAULT
2. function (modify pass through data)
Similar to a ROS topic (named bus)

  ZeroMQ:
Default address listening to pubs: 127.0.0.1:5570
Default address publishing to subs: 127.0.0.1:5571
Sub listen and Pub style: 4+ part envelope (including key)
Subscription Key: all (openface, dnn)
Message parts:
0. sub_key
1. frame
2. timestamp
3. data
4. (data2)

TODO: register somewhere for a bus overview"""

# Copyright (c) Stef van der Struijk.
# License: GNU Lesser General Public License


import sys
import argparse
from functools import partial
import zmq.asyncio
import traceback
import logging
import numpy as np
import json
import queue
# import asyncio

# own import; if statement for documentation
if __name__ == '__main__':
    sys.path.append("..")
    from facsvatarzeromq import FACSvatarZeroMQ
    from smooth_data import SmoothData
else:
    from modules.facsvatarzeromq import FACSvatarZeroMQ
    from .smooth_data import SmoothData


class FACSvatarMessages(FACSvatarZeroMQ):
    """Publishes FACS and Head movement data from .csv files generated by OpenFace"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # keep dict of smooth object per topic
        self.smooth_obj_dict = {}

    # # overwrite existing start function
    # def start(self, async_func_list=None):
    #     """No functions given --> data pass through only; else apply function on data before forwarding
    #
    #     N publishers to 1 sub; proxy 1 sub to 1 pub; publish to M subscribers
    #     """
    #
    #     # make sure pub / sub is initialised
    #     if not self.pub_socket or not self.sub_socket:
    #         print("Both pub and sub needs to be initiliased and set to bind")
    #         print("Pub: {}".format(self.pub_socket))
    #         print("Sub: {}".format(self.sub_socket))
    #         sys.exit()
    #
    #     # apply function to data to passing through data
    #     if async_func_list:
    #         import asyncio
    #         # capture ZeroMQ errors; ZeroMQ using asyncio doesn't print out errors
    #         try:
    #             asyncio.get_event_loop().run_until_complete(asyncio.wait(
    #                 [func() for func in async_func_list]
    #             ))
    #         except Exception as e:
    #             print("Error with async function")
    #             # print(e)
    #             logging.error(traceback.format_exc())
    #             print()
    #
    #         finally:
    #             # TODO disconnect pub/sub
    #             pass
    #
    #     # don't duplicate the message, just pass through
    #     else:
    #         print("Try: Proxy... CONNECT!")
    #         zmq.proxy(self.pub_socket, self.sub_socket)
    #         print("CONNECT successful!")
    
    # converts gaze radians into eye rotation AU values
    def gaze_to_au(self, au_dict, gaze):
        # eye gaze in message as AU
        eye_angle = [gaze['gaze_angle_x'], gaze['gaze_angle_y']]  # radians
        print(eye_angle)
        # eyes go about 60 degree, which is 1.0472 rad, so no conversion needed?

        # set all to 0 (otherwise smoothing problems)
        au_dict['AU61'] = 0
        au_dict['AU62'] = 0
        au_dict['AU63'] = 0
        au_dict['AU64'] = 0

        # eye_angle_x left
        if eye_angle[0] < 0:
            au_dict['AU61'] = min(eye_angle[0]*-1, 1.0)
        # eye_angle_x right
        else:
            au_dict['AU62'] = min(eye_angle[0], 1.0)

        # eye_angle_y up
        if eye_angle[1] >= 0:
            au_dict['AU63'] = min(eye_angle[1], 1.0)
        # eye_angle_y down
        else:
            au_dict['AU64'] = min(eye_angle[1] * -1, 1.0)
            
        return au_dict

    async def pub_sub_function(self, apply_function):  # async
        """Subscribes to FACS data, smooths, publishes it"""

        # # class with data smoothing functions
        # self.smooth_data = SmoothData()
        # # get the function we need to pass data to
        # smooth_func = getattr(self.smooth_data, apply_function)

        new_smooth_object = False

        # await messages
        print("Awaiting FACS data...")
        # without try statement, no error output
        try:
            # keep listening to all published message on topic 'facs'
            while True:
                msg = await self.sub_socket.recv_multipart()
                print()
                print(msg)

                # check not finished; timestamp is empty (b'')
                if msg[1]:
                    msg[2] = json.loads(msg[2].decode('utf-8'))

                    # only pass on messages with enough tracking confidence; always send when no confidence param
                    if 'confidence' not in msg[2] or msg[2]['confidence'] >= 0.7:
                        # subscription key / topic
                        topic = msg[0].decode('ascii')

                        # don't smooth data with 'smooth' == False;
                        if 'smooth' not in msg[2] or msg[2]['smooth']:
                            # if topic changed, instantiate a new SmoothData object
                            if topic not in self.smooth_obj_dict:
                                self.smooth_obj_dict[topic] = SmoothData()
                                new_smooth_object = True

                            # check au dict in data and not empty
                            if "au_r" in msg[2] and msg[2]['au_r']:
                                # convert gaze into AU 61, 62, 63, 64
                                if "gaze" in msg[2]:
                                    msg[2]['au_r'] = self.gaze_to_au(msg[2]['au_r'], msg[2]['gaze'])
                                    # remove from message after AU convert
                                    msg[2].pop('gaze')
                            
                                # sort dict; dicts keep insert order Python 3.6+
                                # au_r_dict = msg[2]['au_r']
                                msg[2]['au_r'] = dict(sorted(msg[2]['au_r'].items(), key=lambda k: k[0]))

                                # match number of multiplier columns:
                                if new_smooth_object:
                                    self.smooth_obj_dict[topic].set_new_multiplier(len(msg[2]['au_r']))
                                    new_smooth_object = False

                                # smooth facial expressions; window_size: number of past data points;
                                # steep: weight newer data
                                # msg[2]['au_r'] = smooth_func(au_r_sorted, queue_no=0, window_size=4, steep=.35)
                                msg[2]['au_r'] = getattr(self.smooth_obj_dict[topic], apply_function)(msg[2]['au_r'],
                                                                                                      queue_no=0,
                                                                                                      window_size=3,
                                                                                                      steep=.25)

                            # check head rotation dict in data and not empty
                            if "pose" in msg[2] and msg[2]['pose']:
                                # smooth head position
                                # msg[2]['pose'] = smooth_func(msg[2]['pose'], queue_no=1, window_size=4, steep=.2)
                                msg[2]['pose'] = getattr(self.smooth_obj_dict[topic], apply_function)(msg[2]['pose'], queue_no=1,
                                                                                     window_size=6,
                                                                                     steep=.15)

                                # TODO add eye direction AU data

                        else:
                            print("No smoothing applied, forwarding unchanged")
                            # remove topic from dict when msgs finish
                            print("Removing topic from smooth_obj_dict: {}".format(self.smooth_obj_dict.pop(topic, None)))

                        # send modified message
                        print(msg)
                        await self.pub_socket.send_multipart([msg[0],  # topic
                                                              msg[1],  # timestamp
                                                              # data in JSON format or empty byte
                                                              json.dumps(msg[2]).encode('utf-8')
                                                              ])

                # send message we're done
                else:
                    print("No more messages to pass; finished")
                    await self.pub_socket.send_multipart([msg[0], b'', b''])

        except:
            print("Error with sub")
            # print(e)
            logging.error(traceback.format_exc())
            print()

    # receive commands
    async def set_parameters(self):
        print("Router awaiting commands")

        while True:
            try:
                [id_dealer, topic, data] = await self.rout_socket.recv_multipart()
                print("Command received from '{}', with topic '{}' and msg '{}'".format(id_dealer, topic, data))

                tp = topic.decode('ascii')
                # set multiplier parameters
                if tp.startswith("multiplier"):
                    await self.set_multiplier(data.decode('utf-8'))
                # elif tp.startswith("dnn"):
                #     await self.set_dnn_user(data.decode('utf-8'))
                else:
                    print("Command ignored")

            except Exception as e:
                print("Error with router function")
                # print(e)
                logging.error(traceback.format_exc())
                print()

    # set new multiplier values
    async def set_multiplier(self, data):
        # JSON to list
        au_multiplier_list = json.loads(data)

        # list to numpy array
        au_multiplier_np = np.array(au_multiplier_list)
        print("New multiplier: {}".format(au_multiplier_np))

        # set new multiplier
        for key, obj in self.smooth_obj_dict.items():
            obj.multiplier = au_multiplier_np


if __name__ == '__main__':
    # command line arguments; sockets have to use bind for N-1-M setup
    parser = argparse.ArgumentParser()

    # subscriber
    parser.add_argument("--sub_ip", default=argparse.SUPPRESS,
                        help="This PC's IP (e.g. 192.168.x.x) pubslishers pub to; Default: 127.0.0.1 (local)")
    parser.add_argument("--sub_port", default="5570",
                        help="Port publishers pub to; Default: 5570")
    parser.add_argument("--sub_bind", default=True,
                        help="True: socket.bind() / False: socket.connect(); Default: True")

    # publisher
    parser.add_argument("--pub_ip", default=argparse.SUPPRESS,
                        help="This PC's IP (e.g. 192.168.x.x) subscribers sub to; Default: 127.0.0.1 (local)")
    parser.add_argument("--pub_port", default="5571",
                        help="Port subscribers sub to; Default: 5571")
    parser.add_argument("--pub_bind", default=True,
                        help="True: socket.bind() / False: socket.connect(); Default: True")

    # router
    parser.add_argument("--rout_ip", default=argparse.SUPPRESS,
                        help="This PC's IP (e.g. 192.168.x.x) router listens to; Default: 127.0.0.1 (local)")
    parser.add_argument("--rout_port", default="5580",
                        help="Port dealers message to; Default: 5580")
    parser.add_argument("--rout_bind", default=True,
                        help="True: socket.bind() / False: socket.connect(); Default: True")

    args, leftovers = parser.parse_known_args()
    print("The following arguments are used: {}".format(args))
    print("The following arguments are ignored: {}\n".format(leftovers))

    # init FACSvatar message class
    facsvatar_messages = FACSvatarMessages(**vars(args))
    # start processing messages; get reference to function without executing
    facsvatar_messages.start([partial(facsvatar_messages.pub_sub_function, "trailing_moving_average"),
                              facsvatar_messages.set_parameters])

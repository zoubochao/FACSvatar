"""Opens a DNN mix bus between 2 ports for a N-proxy-M PubSub pattern

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
else:
    from modules.facsvatarzeromq import FACSvatarZeroMQ


class FACSvatarMessages(FACSvatarZeroMQ):
    """Publishes FACS and Head movement data from .csv files generated by OpenFace"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # keep dict of smooth object per topic
        self.smooth_obj_dict = {}

        # user which is not analysed by DNN; storing head pose / eye blink
        self.dnn_user_store = "p1"

    # TODO work with single user
    async def pub_sub_function(self, apply_function):  # async
        """Subscribes to FACS data, smooths, publishes it"""

        # store data of non-DNN user
        user_data_au = queue.Queue()
        user_data_pose = queue.Queue()

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

                        # check au dict in data and not empty
                        if "au_r" in msg[2] and msg[2]['au_r']:

                            # store data from not DNNed user for merging
                            if not topic.startswith("dnn."):
                                if self.dnn_user_store in topic.split("."):
                                    print("Storing data")
                                    # store data from eye blink AUs
                                    # user_data_au.put({k: v for k, v in au_r_sorted.items() if k in
                                    #                  ['AU45']})
                                    # au_data = {k: msg[2]['au_r'][k] for k in ('AU45',)}
                                    au_data = {k: msg[2]['au_r'][k] for k in ('AU45', 'AU61', 'AU62', 'AU63', 'AU64')
                                               if k in msg[2]['au_r']}
                                    print(au_data)
                                    user_data_au.put(au_data)
                                    print("\n\n")

                            # use stored data for eye blink TODO and eye gaze
                            else:
                                print("DNN uses stored data")
                                # try to override AU data
                                # msg[2]['au_r'] = {**msg[2]['au_r'], **stored_au}
                                try:
                                # stored_au = await user_data_au.get()
                                    msg[2]['au_r'] = {**msg[2]['au_r'], **user_data_au.get_nowait()}
                                except queue.Empty as e:
                                    print("Queue empty")
                                    print(e)
                                    
                                print(msg[2]['au_r'])
                                print()

                        # check head rotation dict in data and not empty
                        if "pose" in msg[2] and msg[2]['pose']:
                            # store data
                            if not topic.startswith("dnn."):
                                # from not DNNed user for merging
                                if self.dnn_user_store in topic.split("."):
                                    print("Storing data")
                                    # store data from eye blink AUs
                                    user_data_pose.put(msg[2]['pose'])

                            # use stored data for head pose
                            else:
                                print("DNN uses stored data")
                                try:
                                    msg[2]['pose'] = {**msg[2]['pose'], **user_data_pose.get_nowait()}
                                except queue.Empty as e:
                                    print("Queue empty")
                                    print(e)
                                    
                                print(msg[2]['pose'])
                                print()

                        # add target user to display data (and not display original data)
                        msg[2]['user_ignore'] = self.dnn_user_store

                        # send modified message
                        print(msg)
                        await self.pub_socket.send_multipart([msg[0],  # topic
                                                              msg[1],  # timestamp
                                                              # data in JSON format or empty byte
                                                              json.dumps(msg[2]).encode('utf-8')
                                                              ])
                                                              
                    else:
                        print("Not enough tracking confidence to forward message")

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
                if tp.startswith("dnn"):
                    await self.set_dnn_user(data.decode('utf-8'))
                else:
                    print("Command ignored")

            except Exception as e:
                print("Error with router function")
                # print(e)
                logging.error(traceback.format_exc())
                print()

    async def set_dnn_user(self, user_key):
        print("Was storing data for: {}".format(self.dnn_user_store))

        # store data of not DNNed user
        if user_key == "p0":
            user_store = "p1"
        elif user_key == "p1":
            user_store = "p0"
        # don't change
        else:
            user_store = self.dnn_user_store
            print("user_key is not p0 or p1")

        print("Now storing data for: {}".format(self.dnn_user_store))
        self.dnn_user_store = user_store


if __name__ == '__main__':
    # command line arguments; sockets have to use bind for N-1-M setup
    parser = argparse.ArgumentParser()

    # subscriber
    parser.add_argument("--sub_ip", default=argparse.SUPPRESS,
                        help="This PC's IP (e.g. 192.168.x.x) pubslishers pub to; Default: 127.0.0.1 (local)")
    parser.add_argument("--sub_port", default="5569",
                        help="Port publishers pub to; Default: 5569")
    parser.add_argument("--sub_bind", default=True,
                        help="True: socket.bind() / False: socket.connect(); Default: True")

    # publisher
    parser.add_argument("--pub_ip", default=argparse.SUPPRESS,
                        help="This PC's IP (e.g. 192.168.x.x) subscribers sub to; Default: 127.0.0.1 (local)")
    parser.add_argument("--pub_port", default="5570",
                        help="Port subscribers sub to; Default: 5570")
    parser.add_argument("--pub_bind", default=False,
                        help="True: socket.bind() / False: socket.connect(); Default: False")

    # router
    parser.add_argument("--rout_ip", default=argparse.SUPPRESS,
                        help="This PC's IP (e.g. 192.168.x.x) router listens to; Default: 127.0.0.1 (local)")
    parser.add_argument("--rout_port", default="5582",
                        help="Port dealers message to; Default: 5582")
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

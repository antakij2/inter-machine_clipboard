# TODO: does the Lock() work properly?
import os
import sys
if sys.stderr is None:
    logfile = os.path.join(os.environ['TEMP'], 'imc_server.log')
    sys.stderr = open(logfile, 'a')
if sys.stdout is None:
    sys.stdout = sys.stderr

import eventlet 
eventlet.monkey_patch()

import socketio
from flask import Flask, send_file, request
import re
import threading
import json
from argparse import ArgumentParser as AP

flapp = Flask(__name__)

CLIPBOARD_FILE = 'clipboard'
METADATA_FILE = 'metadata.json'
CLIPBOARD = 'clipboard'
MIMETYPE = 'mimetype'
FILENAME = 'filename'
TEMP_CLIPBOARD_FILE = 'temp_clipboard'
TEMP_METADATA_FILE = 'temp_mimetype'

rtfd_pattern_beginning = re.compile(rb'^rtfd.+?(?={)', flags=re.DOTALL) #TODO: explicitly encode these as utf-8?
rtfd_pattern_end = re.compile(rb'(?<=})[^}]+$', re.DOTALL)

receive_clipboard_data_lock = threading.Lock()

# Ensure the necessary files exist
if not os.path.isfile(CLIPBOARD_FILE):
    with open(CLIPBOARD_FILE, 'wb') as file:
        pass

if not os.path.isfile(METADATA_FILE):
    with open(METADATA_FILE, 'w', encoding='utf-8') as file:
        json.dump({MIMETYPE : 'text/plain', FILENAME : 'clipboard'}, file)


@flapp.get('/')
def send_clipboard_data():
    '''
    print('=v=v=v=v=v=REQUEST=v=v=v=v=v=v')
    print(request.headers)
    print('=^=^=^=^=^=REQUEST=^=^=^=^=^=^')
    '''
    print("sending clipboard data")
    with open(METADATA_FILE, encoding='utf-8') as file:
        #TODO: D: limit size of data to prevent json injection attack?
        metadata = json.load(file)
        return send_file(CLIPBOARD_FILE, mimetype=metadata[MIMETYPE], download_name=metadata[FILENAME], as_attachment=True)


@flapp.put('/')
def receive_clipboard_data_from_PUT():
    print("receiving clipboard data from PUT\n")
    print('=v=v=v=v=v=REQUEST=v=v=v=v=v=v')
    print(request.headers)
    print('***************************************************************************')
    print(len(request.data))
    print('=^=^=^=^=^=REQUEST=^=^=^=^=^=^')
    return receive_clipboard_data_workhorse(
        None,
        request.headers.get('Content-Type', ''),
        request.headers.get('Filename', 'clipboard'),
        request.data
    )

def receive_clipboard_data_workhorse(sending_sid, incoming_mimetype, incoming_filename, incoming_data):
    with receive_clipboard_data_lock:
        # Write to temp files, and then replace the current files with the temp files

        # If incoming clipboard data is rtfd data (copied from an iMessage text), convert it to normal rtf data
        # TODO: what if incoming_data is not (rich) text, but happens to start with the bytes for 'rtfd...'
        #  and gives a false positive?
        match_beginning = re.search(rtfd_pattern_beginning, incoming_data)
        match_end = re.search(rtfd_pattern_end, incoming_data)
        if match_beginning and match_end:
            incoming_data = incoming_data[match_beginning.end():match_end.start()]
            incoming_mimetype = 'text/rtf'

        # TODO: fsync these temp files?
        with open(TEMP_METADATA_FILE, 'w', encoding='utf-8') as file:
            # TODO: D: limit size of data to prevent json injection attack?
            json.dump({MIMETYPE : incoming_mimetype, FILENAME : incoming_filename}, file)

        with open(TEMP_CLIPBOARD_FILE, 'wb') as file:
            file.write(incoming_data)

        # Back up current metadata in case metadata file needs to be rolled back
        with open(METADATA_FILE, encoding='utf-8') as file:
            metadata_backup = json.load(file)

        os.replace(TEMP_METADATA_FILE, METADATA_FILE)
        # No rollback necessary if anything up to this point failed
        try:
            # TODO: test this rollback behavior
            os.replace(TEMP_CLIPBOARD_FILE, CLIPBOARD_FILE)
        except Exception:
            # If updating the clipboard file failed, roll back the changes to the metadata file
            with open(METADATA_FILE, 'w', encoding='utf-8') as file: #TODO: what if this fails too? SQLite better for this?
                json.dump(metadata_backup, file)

            raise

        # Push new clipboard data to all devices listening via websocket, except the device that sent 
        #  the new clipboard data via websocket, if there is one
        print('Pushing new data to all but', sending_sid)
        with open(METADATA_FILE, encoding='utf-8') as metadata_file, open(CLIPBOARD_FILE, 'rb') as clipboard_file:
            metadata = json.load(metadata_file)
            server.emit(
                    'server to client',
                    {
                        MIMETYPE : metadata[MIMETYPE],
                        FILENAME : metadata[FILENAME],
                        CLIPBOARD : clipboard_file.read()
                    },
                    skip_sid=sending_sid
            )
        
        return 'Data received successfully.'

'''
@flapp.after_request
def show_reponse(res):
    print('=v=v=v=v=v=RESPONSE=v=v=v=v=v=v')
    print(res.headers)
    print('=^=^=^=^=^=RESPONSE=^=^=^=^=^=^')
    return res
'''

server = socketio.Server(logger=True, engineio_logger=True, max_http_buffer_size=10 << 30)
app = socketio.WSGIApp(server, flapp)

@server.event
def connect(sid, environ, auth):
    print('websocket connect', sid)

@server.on('client to server')
def receive_clipboard_data_from_websocket(sid, data):
    print(f'{data[MIMETYPE]=}')
    print(f'{data[FILENAME]=}')
    print(f'{len(data[CLIPBOARD])=}')
    receive_clipboard_data_workhorse(sid, data[MIMETYPE], data[FILENAME], data[CLIPBOARD])

DEFAULT_ADDRESS = '0.0.0.0'
DEFAULT_PORT = 3000

parser = AP(description='Inter-machine Clipboard server')
parser.add_argument('-a', '--address', default=DEFAULT_ADDRESS, help=f'the ip address to listen on (default {DEFAULT_ADDRESS})')
parser.add_argument('-p', '--port', default=DEFAULT_PORT, type=int, help=f'the port to listen on (default {DEFAULT_PORT})')
args = parser.parse_args()

#TODO: shut down properly
eventlet.wsgi.server(eventlet.listen((args.address, args.port)), app)

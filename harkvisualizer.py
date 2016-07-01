import atexit
from datetime import timedelta
import simplejson as json
import logging as log
from os import path, remove, listdir
from time import sleep

import pyhark.saas
import speech_recognition

from tornado import web, ioloop, websocket
from werkzeug.utils import secure_filename

STAGING_AREA = '/tmp/'
ALLOWED_EXTENSIONS = set(['flac', 'wav'])
STATIC_PATH = 'static'
HTML_TEMPLATE_PATH = 'templates'
LISTEN_PORT = 80
LANGUAGE = 'ja-JP'

log.basicConfig(
    level=log.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

settings = {
'static_path': path.join(path.dirname(__file__), STATIC_PATH),
'template_path': path.join(path.dirname(__file__), HTML_TEMPLATE_PATH)
}

default_hark_config = {
    'processType': 'batch',
    'params': {
        'numSounds': 2,
        'roomName': 'sample room',
        'micName': 'dome',
        'thresh': 21
    },
    'sources': [
        {'from': -180, 'to': 0},
        {'from': 0, 'to': 180},
    ]
}


class HttpRequestHandler(web.RequestHandler):


    def get(self):
        self.render('index.html')

    def post(self):
        if 'file' not in self.request.files:
            self.render('index.html')
        file = self.request.files['file'][0]
        if file and allowed_file(file['filename']):
            file_name = secure_filename(file['filename'])
            audio_file = STAGING_AREA + file_name
            write_handle = open(audio_file, 'w')
            write_handle.write(file['body'])
            read_handle = open(audio_file, 'rb')
            hark.client.createSession(default_hark_config)
            hark.upload_file(read_handle)
            self.render('visualize.html')

# Wrapper around PyHarkSaas
class Hark:


    def __init__(self):
        log.info('Initializing hark client')
        auth = json.load(open('harkauth.json'))
        client = pyhark.saas.PyHarkSaaS(auth['apikey'], auth['apisec'])
        client.login()
        self.client = client

    def get_audio(self, srcID, file_name):
        log.info('Retrieving separated audio %d from hark', srcID)
        with open(file_name, 'w') as write_handle:
            self.client.getSeparatedAudio(handle=write_handle,
                                          srcID=srcID)

    def delete_session(self):
        log.info('Deleting hark session %s', self.client.getSessionID())
        self.client.deleteSession()

    def upload_file(self, file_handle):
        log.info('Uploading file %s to hark', file_handle.name)
        self.client.uploadFile(file_handle)

    def get_results(self):
        return self.client.getResults()

    def wait(self):
        self.client.wait()

    def is_finished(self):
        return self.client.isFinished()


# Wrapper around SpeechRecognition
class Speech:


    def __init__(self):
        log.info('Initializing speech client')
        self.auth = str(json.load(open('bingauth.json'))['apikey'])
        self.client = speech_recognition.Recognizer()

    def translate(self, file_name):
        transcription = 'Inaudible'
        with speech_recognition.AudioFile(path.join(path.dirname(
            path.realpath(__file__)), file_name)) as source:
            audio = self.client.record(source)
        log.info('Sending separated audio file to speech api')
        try:
            transcription = self.client.recognize_bing(audio,
                key=self.auth,
                language=LANGUAGE)
        # This exception should pass if there is no transcription.
        except speech_recognition.UnknownValueError:
            pass
        log.info('Transcription: %s', transcription)
        return transcription


class WebSocketHandler(websocket.WebSocketHandler):


    # Allow cross-origin web socket connections
    def check_origin(self, origin):
        return True

    # Invoked when socket closes or program exits
    def on_connection_close(self):
        log.info('Web socket connection closed')
        clean_staging()

    # Invoked when socket is opened
    def open(self):
        log.info('Web socket connection established')
        # ioloop to wait for 2 seconds before sending data 
        ioloop.IOLoop.instance().add_timeout(timedelta(seconds=2),
                                             self.send_data)

    def send_data(self):
        if hark.is_finished():
            self.close()
        data = hark.get_results()
        log.info('Latest hark analysis results:')
        log.info(str(data['context']))
        # If there is context data available, send it to browser w/o memoization
        if data['context']:
            self.write_message(json.dumps(data))

        ioloop.IOLoop.instance().add_timeout(timedelta(seconds=1), self.send_data)


# Helper functions
def allowed_file(file_name):
    extension = file_name.rsplit('.', 1)[1]
    return '.' in file_name and extension in ALLOWED_EXTENSIONS

def clean_staging():
   remove_all(STAGING_AREA)
   hark.delete_session()

def get_app():
    application = web.Application([
        (r'/', HttpRequestHandler),
        (r'/websocket', WebSocketHandler),
        (r'/(apple-touch-icon\.png)', web.StaticFileHandler,
            dict(path=settings['static_path'])),
        ], **settings)
    return application

def remove_all(dir):
    log.info('Deleting contents of %s', dir)
    files = listdir(dir)
    for file in files:
        remove(dir + file)


if __name__ == '__main__':
    hark = Hark()
    speech = Speech()
    atexit.register(clean_staging)
    log.info('Initializing web application')
    app = get_app()
    app.listen(LISTEN_PORT)
    ioloop.IOLoop.instance().start()

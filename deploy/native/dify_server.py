"""使用上游gevent/Socket.IO入口，仅把监听收紧为本机隔离端口。"""

from gevent import monkey

monkey.patch_all()

import sys
import psycogreen.gevent
from grpc.experimental import gevent as grpc_gevent

grpc_gevent.init_gevent()
psycogreen.gevent.patch_psycopg()

from app import socketio_app
from gevent import pywsgi
from geventwebsocket.handler import WebSocketHandler

if __name__ == '__main__':
    pywsgi.WSGIServer(('127.0.0.1', int(sys.argv[1])), socketio_app, handler_class=WebSocketHandler).serve_forever()

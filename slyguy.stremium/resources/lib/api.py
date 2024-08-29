from time import time

from slyguy import userdata
from slyguy.session import Session
from slyguy.exceptions import Error

from .constants import *
from .language import _


class APIError(Error):
    pass


class API(object):
    def new_session(self):
        self.logged_in = False
        self._session = Session(base_url=API_URL, headers=HEADERS)
        self._set_authentication()

    def _set_authentication(self):
        access_token = userdata.get('access_token')
        if not access_token:
            return

        self._session.headers.update({'authorization': access_token})
        self.logged_in = True

    def _refresh_token(self, force=False):
        refresh_token = userdata.get('refresh_token')
        if not refresh_token or (not force and userdata.get('expires', 0) > time()):
            return

        params = {
            'key': GOOGLE_KEY,
        }

        payload = {
            'grantType': 'refresh_token',
            'refreshToken': refresh_token,
        }

        data = self._session.post(TOKEN_URL, params=params, json=payload).json()
        if 'error' in data:
            self.logout()
            raise APIError(data['error']['message'])

        userdata.set('access_token', data['access_token'])
        userdata.set('refresh_token', data['refresh_token'])
        userdata.set('expires', int(time()) + int(data['expires_in']) - 30)
        self._set_authentication()

    def user(self):
        self._refresh_token()
        params = {'cardInfo': 'true'}
        return self._session.get('/user', params=params).json()

    def favorites(self):
        self._refresh_token()
        return self._session.get('/favoriteChannels').json()

    def add_favorite(self, channel_id):
        self._refresh_token()
        resp = self._session.put('/favoriteChannels', params={'channelId': channel_id})
        if resp.ok:
            return True

        data = resp.json()
        if 'error' in data:
            raise APIError(data['error'])

        return False

    def del_favorite(self, channel_id):
        self._refresh_token()
        resp = self._session.delete('/favoriteChannels', params={'channelId': channel_id})
        if resp.ok:
            return True

        data = resp.json()
        if 'error' in data:
            raise APIError(data['error'])

        return False

    def channels(self):
        self._refresh_token()

        params = {
            'noGuideData': True,
        }

        return self._session.get('/programGuide', params=params).json()

    def play(self, id):
        self._refresh_token()

        params = {
            'channelId': id,
        }

        data = self._session.get('/playbackAuthenticated', params=params).json()
        if 'url' not in data:
            raise APIError(data.get('errorMessage'))

        return data

    def epg(self, id=None, date=None):
        self._refresh_token()

        params = {}
        if id:
            params['channelId'] = id

        if date:
            params['dateKey'] = date.format('YYYYMMDD')

        return self._session.get('/programGuide', params=params).json()

    def login(self, email, password):
        self.logout()

        params = {
            'key': GOOGLE_KEY,
        }

        payload = {
            'email': email,
            'password': password,
            'returnSecureToken': True,
        }

        data = self._session.post(LOGIN_URL, params=params, json=payload).json()
        if 'error' in data:
            raise APIError(data['error']['message'])

        userdata.set('refresh_token', data['refreshToken'])
        self._refresh_token(force=True)

    def device_code(self):
        self.logout()
        return self._session.post('/externalToken').text

    def device_login(self, code):
        params = {
            'code': code,
        }
        resp = self._session.get('/externalToken', params=params)
        if not resp.ok:
            return False

        token = resp.json()['token']

        params = {
            'key': GOOGLE_KEY,
        }
        payload = {
            'token': token,
            'returnSecureToken': True,
        }

        data = self._session.post(VERIFY_TOKEN, params=params, json=payload).json()
        if 'error' in data:
            raise APIError(data['error']['message'])

        userdata.set('refresh_token', data['refreshToken'])
        self._refresh_token(force=True)
        return True

    def logout(self):
        userdata.delete('access_token')
        userdata.delete('refresh_token')
        userdata.delete('expires')
        self.new_session()

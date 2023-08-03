HEADERS = {
    'user-agent': 'okhttp/3.12.1',
}

LOGIN_URL = 'https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyPassword'
TOKEN_URL = 'https://securetoken.googleapis.com/v1/token'
VERIFY_TOKEN = 'https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyCustomToken'

GOOGLE_KEY = 'AIzaSyBhQW6GEk_QKfDAX_mP7sG9Vcnju6kLszg'
API_URL = 'https://api.stremium.com{}'
IMAGE_URL = 'https://i.mjh.nz/.images/stremium/'
DEVICE_CODE_URL = 'https://stremium.com/link'

ALL = '_'
PUBLIC = 'public'
CUSTOM = 'custom'
MY_CHANNELS = 'my_channels'

PROVIDER_ART = {
    CUSTOM: None,
    PUBLIC: None,
    'locast': IMAGE_URL+'locast.png',
    'philo': IMAGE_URL+'philo.png',
    'spectrum': IMAGE_URL+'spectrum.png',
    'frndlytv': IMAGE_URL+'frndlytv.png',
    'sling': IMAGE_URL+'sling.png',
    'vidgo': IMAGE_URL+'vidgo.png',
}

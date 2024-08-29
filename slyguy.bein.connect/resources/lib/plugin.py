import codecs
from xml.sax.saxutils import escape

import arrow
from slyguy import plugin, gui, userdata, signals, inputstream
from slyguy.constants import MIDDLEWARE_REGEX, KODI_VERSION

from .api import API
from .language import _
from .constants import *
from .settings import settings


api = API()


@signals.on(signals.BEFORE_DISPATCH)
def before_dispatch():
    api.new_session()
    plugin.logged_in = api.logged_in

@plugin.route('')
def home(**kwargs):
    folder = plugin.Folder(cacheToDisc=False)

    if not api.logged_in:
        folder.add_item(label=_(_.LOGIN, _bold=True), path=plugin.url_for(login), bookmark=False)
    else:
        folder.add_item(label=_(_.LIVE_TV, _bold=True), path=plugin.url_for(live))

        if settings.getBool('bookmarks', True):
            folder.add_item(label=_(_.BOOKMARKS, _bold=True), path=plugin.url_for(plugin.ROUTE_BOOKMARKS), bookmark=False)

        folder.add_item(label=_.LOGOUT, path=plugin.url_for(logout), _kiosk=False, bookmark=False)

    folder.add_item(label=_.SETTINGS, path=plugin.url_for(plugin.ROUTE_SETTINGS), _kiosk=False, bookmark=False)
    return folder


@plugin.route()
def login(**kwargs):
    options = [
        [_.LOGIN_CONNECT, LOGIN_CONNECT],
        [_.LOGIN_SATELLITE, LOGIN_SATELLITE],
    ]

    index = 0 if len(options) == 1 else gui.context_menu([x[0] for x in options])
    if index == -1:
        return

    username = gui.input(_.ASK_USERNAME, default=userdata.get('username', '')).strip()
    if not username:
        return

    userdata.set('username', username)

    password = gui.input(_.ASK_PASSWORD, hide_input=True).strip()
    if not password:
        return

    api.login(username, password, _type=options[index][1])
    gui.refresh()

def _live_channels():
    channels = []
    for row in api.channels():
        if not api.logged_in and not row['isFta']:
            continue
        channels.append(row)
    return channels

@plugin.route()
def live(**kwargs):
    folder = plugin.Folder(_.LIVE_TV)

    start = arrow.utcnow().shift(hours=2)
    end = arrow.utcnow()

    channels = _live_channels()
    ids = [x['idChannel'] for x in channels]
    epg = api.epg(ids, {'startutc': {'$le': start.timestamp}}, {'endutc': {'$ge': end.timestamp}})

    now = arrow.now()
    epg_count = 5
    for row in channels:
        plot = u''
        count = 0
        if epg_count:
            for program in epg.get(row['idChannel'], []):
                start = arrow.get(program['startutc'])
                stop = arrow.get(program['endutc'])

                if (now > start and now < stop) or start > now:
                    plot += u'[{}] {}\n'.format(start.to('local').format('h:mma'), program['title'])
                    count += 1
                    if count == epg_count:
                        break

        if not plot:
            plot = row.get('synopsis')

        folder.add_item(
            label = row['name'],
            art = {'thumb': row.get('logo')},
            info = {'plot': plot},
            path = plugin.url_for(play, channel_id=row['idChannel'], _is_live=True),
            playable = True,
        )

    return folder

@plugin.route()
@plugin.plugin_request()
def license_request(channel_id, **kwargs):
    url, headers = api.license_request(channel_id)
    return {'url': url, 'headers': headers}

@plugin.route()
@plugin.no_error_gui()
def heartbeat(channel_id, **kwargs):
    api.heartbeat(channel_id)

@plugin.route()
@plugin.login_required()
def play(channel_id, **kwargs):
    if channel_id == '267' and KODI_VERSION < 20:
        gui.ok('This stream requires Kodi 20 to playback correctly')

    url = api.play(channel_id)
    license_path = plugin.url_for(license_request, channel_id=channel_id)

    return plugin.Item(
        path = url,
        inputstream = inputstream.Widevine(license_key=license_path, challenge='b{SSM}', response='B'),
        headers = HEADERS,
        proxy_data = {
            'default_language': settings.get('default_language'),
            'middleware': {license_path: {'type': MIDDLEWARE_REGEX, 'pattern': '<LICENSE>(.*?)</LICENSE>'}},
        },
        callback = {
            'type':'interval_ts',
            'interval': 60*5,
            'callback': plugin.url_for(heartbeat, channel_id=channel_id),
        }
    )

@plugin.route()
def logout(**kwargs):
    if not gui.yes_no(_.LOGOUT_YES_NO):
        return

    api.logout()
    gui.refresh()

@plugin.route()
@plugin.merge()
@plugin.login_required()
def playlist(output, **kwargs):
    channels = api.channels()

    with codecs.open(output, 'w', encoding='utf8') as f:
        f.write(u'#EXTM3U x-tvg-url="{}"'.format(plugin.url_for(epg, output='$FILE')))

        for row in channels:
            if not api.logged_in and not row['isFta']:
                continue

            f.write(u'\n#EXTINF:-1 tvg-chno="{chno}" tvg-id="{id}" tvg-name="{name}" tvg-logo="{logo}",{name}\n{url}'.format(
                chno=row['localizeNumber'], id=row['idChannel'], name=row.get('epg_name', row['name']), logo=row.get('logo'), url=plugin.url_for(play, channel_id=row['idChannel'], _is_live=True,
            )))

@plugin.route()
@plugin.merge()
@plugin.login_required()
def epg(output, **kwargs):
    with codecs.open(output, 'w', encoding='utf8') as f:
        f.write(u'<?xml version="1.0" encoding="utf-8" ?><tv>')

        ids = []
        for row in _live_channels():
            f.write(u'<channel id="{}"><display-name>{}</display-name><icon src="{}"/></channel>'.format(
                row['idChannel'], escape(row['name']), escape(row.get('logo'))))
            ids.append(row['idChannel'])

        start = arrow.utcnow().shift(hours=-12)
        end = arrow.utcnow().shift(days=settings.getInt('epg_days', 3))
        chunksize = 10

        def chunks(lst, n):
            for i in range(0, len(lst), n):
                yield lst[i:i + n]

        for chunk in chunks(ids, chunksize):
            epg = api.epg(chunk, {'startutc': {'$ge': start.timestamp}}, {'startutc': {'$lt': end.timestamp}})

            for channel_id in epg:
                for event in epg[channel_id]:
                    f.write(u'<programme channel="{}" start="{}" stop="{}"><title>{}</title><desc>{}</desc>{}</programme>'.format(
                        channel_id, arrow.get(event['startutc']).format('YYYYMMDDHHmmss Z'), arrow.get(event['endutc']).format('YYYYMMDDHHmmss Z'),
                            escape(event.get('title') or ''), escape(event.get('synopsis') or ''), u'<category>{}</category>'.format(escape(event.get('genre') or ''))))

        f.write(u'</tv>')

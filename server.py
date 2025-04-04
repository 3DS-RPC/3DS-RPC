# Created by Deltaion Lee (MCMi460) on Github
from flask import Flask, make_response, request, redirect, render_template, send_file
from flask_limiter import Limiter
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
import sys, datetime, xmltodict, pickle, secrets

from sqlalchemy import select, update, insert, delete

from api.love2 import *
from api.private import CLIENT_ID, CLIENT_SECRET, HOST
from api.public import pretendoBotFC, nintendoBotFC
from api.networks import NetworkType, nameToNetworkType
from database import *

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = get_db_url()

db = SQLAlchemy()
db.init_app(app)

# We need to manually specify our base model's metadata after
# importing all other models using it as the declarative base.
# (See above with `from database import *`)
migrate = Migrate(app, db, target_metadata=Base.metadata)

limiter = Limiter(app, key_func=lambda: request.access_route[-1])

API_ENDPOINT: str = 'https://discord.com/api/v10'

local = False
port = 2277
version = 0.31
agent = '3DS-RPC/'

frontend_uptime = datetime.datetime.now()
start_db_time(None, NetworkType.NINTENDO)
start_db_time(None, NetworkType.PRETENDO)


@app.errorhandler(404)
def handler404(e):
    return render_template('dist/404.html')


disable_backend_warnings = False
try:
    if sys.argv[1] == 'ignoreBackend' and local:
        disable_backend_warnings = True
except:
    pass

if local:
    HOST = 'http://localhost:2277'

# Limiter limits
user_presence_limit = '3/minute'
new_user_limit = '2/minute'
cdn_limit = '60/minute'
toggler_limit = '5/minute'

# Database files
title_database = []
titles_to_uid = []

requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)


# Create title cache
def cache_titles():
    global title_database, titles_to_uid

    # Pull databases
    database_path = './cache/'
    if not os.path.exists(database_path):
        os.mkdir(database_path)
    database_path = os.path.join(database_path, 'databases.dat')
    if os.path.isfile(database_path):
        with open(database_path, 'rb') as file:
            t = pickle.loads(file.read())
            title_database = t[0]
            titles_to_uid = t[1]
    else:
        title_database = []
        titles_to_uid = []

        # Create progress bar
        bar = ProgressBar()

        for region in ['US', 'JP', 'GB', 'KR', 'TW']:
            title_database.append(
                xmltodict.parse(requests.get('https://samurai.ctr.shop.nintendo.net/samurai/ws/%s/titles?shop_id=1&limit=5000&offset=0' % region, verify = False).text)
            )

            # Update progress bar as database requests complete
            bar.update(.5 / 5)
            titles_to_uid += requests.get('https://raw.githubusercontent.com/hax0kartik/3dsdb/master/jsons/list_%s.json' % region).json()
            bar.update(.5 / 5)

        bar.end() # End the progress bar

        # Save databases to file
        with open(database_path, 'wb') as file:
            file.write(pickle.dumps(
                (title_database,
                 titles_to_uid)
            ))
        print('[Saved database to file]')


# Create entry in database with friendCode
def create_user(friend_code: int, network: NetworkType, add_new_instance: bool):
    # Make sure the user isn't trying to create any registered bot friend code.
    if int(friend_code) == int(pretendoBotFC):
        raise Exception('invalid FC')
    if int(friend_code) == int(nintendoBotFC):
        raise Exception('invalid FC')

    try:
        if not add_new_instance:
            raise Exception('UNIQUE constraint failed: friends.friendCode')
        already_added_check = db.session.scalar(
            select(Friend)
            .where(Friend.friend_code == str(friend_code).zfill(12))
            .where(Friend.network == network)
        )
        if already_added_check:
            raise Exception('UNIQUE constraint failed: friends.friendCode')
        db.session.add(Friend(
            friend_code=str(friend_code).zfill(12),
            network=network,
            online=False,
            title_id='0',
            upd_id='0',
            last_accessed=time.time() + 300,
            account_creation=time.time(),
            last_online=time.time(),
            favorite_game=0
        ))
        db.session.commit()
    except Exception as e:
        if 'UNIQUE constraint failed: friends.friendCode' in str(e):
            db.session.execute(
                update(Friend)
                .where(Friend.friend_code == str(friend_code).zfill(12))
                .where(Friend.network == network)
                .values(last_accessed=time.time())
            )
            db.session.commit()


def fetch_bearer_token(code: str):
    data = {
        'client_id': '%s' % CLIENT_ID,
        'client_secret': '%s' % CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': '%s/authorize' % HOST,
    }
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    r = requests.post('%s/oauth2/token' % API_ENDPOINT, data=data, headers=headers)
    r.raise_for_status()
    return r.json()


def refresh_bearer(token: str):
    user = user_from_token(token)
    data = {
        'client_id': '%s' % CLIENT_ID,
        'client_secret': '%s' % CLIENT_SECRET,
        'grant_type': 'refresh_token',
        'refresh_token': user.refresh_token,
    }
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    r = requests.post('%s/oauth2/token' % API_ENDPOINT, data=data, headers=headers)
    r.raise_for_status()
    token, user, pfp = create_discord_user('', r.json())
    return token, user, pfp


def token_from_id(discord_id: int) -> str:
    stmt = select(Discord).where(Discord.id == discord_id)
    result = db.session.scalar(stmt)
    return result.site_session_token


def user_from_token(token: str) -> Discord:
    stmt = select(Discord).where(Discord.site_session_token == token)
    result = db.session.scalar(stmt)
    if not result:
        raise Exception('invalid token!')
    return result


def create_discord_user(code: str, response: dict = None):
    if not response:
        response = fetch_bearer_token(code)
    headers = {
        'Authorization': 'Bearer %s' % response['access_token'],
    }
    new = requests.get('https://discord.com/api/users/@me', headers=headers)
    user = new.json()
    token = secrets.token_hex(20)
    try:
        already_exist_check = db.session.scalar(
            select(Discord)
            .where(Discord.id == user['id'])
        )
        if already_exist_check:
            raise Exception('UNIQUE constraint failed: discord.id')
        db.session.add(Discord(
            id=user['id'],
            refresh_token=response['refresh_token'],
            bearer_token=response['access_token'],
            rpc_session_token=None,
            site_session_token=token,
            last_accessed=0,
            generation_date=time.time()
        ))
        db.session.commit()
    except Exception as e:
        if 'UNIQUE constraint failed' in str(e):
            old_token = token_from_id(user['id'])

            discord_user = user_from_token(old_token)
            discord_user.refresh_token = response['refresh_token']
            discord_user.bearer_token = response['access_token']
            discord_user.generation_date = time.time()
            discord_user.site_session_token = token

            db.session.commit()

    if user['avatar']:
        if user['avatar'].startswith('a_'):
            avatar_extension = 'gif'
        else:
            avatar_extension = 'png'
        avatar_url = 'https://cdn.discordapp.com/avatars/%s/%s.%s' % (user['id'], user['avatar'], avatar_extension)
    else:
        avatar_url = ''

    return token, user['username'], avatar_url


def delete_discord_user(discord_id: int):
    db.session.delete(db.session.get(Discord, discord_id))
    db.session.delete(db.session.get(DiscordFriends, discord_id))
    db.session.commit()


def get_connected_consoles(discord_id: int):
    stmt = select(DiscordFriends).where(DiscordFriends.id == discord_id)
    result = db.session.scalars(stmt).all()
    
    return [(result.friend_code, result.active, result.network) for result in result]


def sidenav():
    nintendo_start_time = db.session.get(Config, NetworkType.NINTENDO).backend_uptime
    pretendo_start_time = db.session.get(Config, NetworkType.PRETENDO).backend_uptime

    status = 'Offline'
    if nintendo_start_time is not None and pretendo_start_time is not None:
        status = 'Operational'
    elif (nintendo_start_time is not None and pretendo_start_time is None) or (nintendo_start_time is None and pretendo_start_time is not None):
        status = 'Semi-Operational'

    # Get a human-readable uptime.
    time_now = datetime.datetime.now()
    if nintendo_start_time is not None:
        nintendo_status = 'Nintendo Backend has been up for %s...' % str(time_now - nintendo_start_time)[:-7]
    else:
        nintendo_status = 'Nintendo Backend: Offline'

    if pretendo_start_time is not None:
        pretendo_status = 'Pretendo Backend has been up for %s...' % str(time_now - pretendo_start_time)[:-7]
    else:
        pretendo_status = 'Pretendo Backend: Offline'

    # Trim off microseconds
    frontend_status = str(datetime.datetime.now() - frontend_uptime)[:-7]

    data = {
        'uptime': frontend_status,
        'nintendo-uptime-backend': nintendo_status,
        'pretendo-uptime-backend': pretendo_status,
        'status': status,
    }
    return data


def user_agent_check():
    user_agent = request.headers['User-Agent']
    try:
        if float(user_agent.replace(agent, '')) != version:
            raise Exception('client is not v%s' % version)
    except:
        raise Exception('this client is invalid')


def get_presence(friend_code: int, network: NetworkType, is_api: bool):
    try:
        if is_api:
            # First, run 3DS-RPC client checks.
            user_agent_check()

            # Create a user for this friend code, or update its last access date.
            # TODO(spotlightishere): This should be restructured!
            create_user(friend_code, network, False)

        network_start_time = db.session.get(Config, network).backend_uptime
        if network_start_time is None and not disable_backend_warnings:
            raise Exception('Backend currently offline. please try again later')

        friend_code = str(friend_code).zfill(12)
        principal_id = friend_code_to_principal_id(friend_code)
        stmt = (
            select(Friend)
            .where(Friend.friend_code == friend_code)
            .where(Friend.network == network)
        )
        result = db.session.scalar(stmt)

        if not result:
            raise Exception('Friend code not recognized!\nHint: You may not have added the bot as a friend')
        if result.online:
            presence = {
                'titleID': result.title_id,
                'updateID': result.upd_id,
                'joinable': result.joinable,
                'gameDescription': result.game_description,
                'game': getTitle(result.title_id, titles_to_uid, title_database),
                'disclaimer': 'all information regarding the title (User/Presence/game) is downloaded from Nintendo APIs',
            }
        else:
            presence = {}
        mii = result.mii
        if mii:
            mii = MiiData().mii_studio_url(mii)
        return {
            'Exception': False,
            'User': {
                'principalId': principal_id,
                'friendCode': str(friend_code).zfill(12),
                'online': result.online,
                'Presence': presence,
                'username': result.username,
                'message': result.message,
                'mii': mii,
                'accountCreation': result.account_creation,
                'lastAccessed': result.last_accessed,
                'lastOnline': result.last_online,
                'favoriteGame': result.favorite_game,
            }
        }
    except Exception as e:
        return {
            'Exception': {
                'Error': str(e),
            }
        }

##################
# NON-API ROUTES #
##################


# Index page
@app.route('/')
def index():
    stmt = (
        select(Friend)
        .where(Friend.online == True)
        .where(Friend.username != None)
        .order_by(Friend.last_accessed.desc())
        )
    results = db.session.scalars(stmt).all()
    num = len(results)
    data = sidenav()
    data['active'] = [({
        'mii': MiiData().mii_studio_url(user.mii),
        'username': user.username,
        'game': getTitle(user.title_id, titles_to_uid, title_database),
        'friendCode': user.friend_code.zfill(12),
        'joinable': user.joinable,
        'network': user.network.lower_name(),
    }) for user in results if user.username]
    data['active'] = data['active'][:2]

    stmt = (
        select(Friend)
        .where(Friend.username != None)
        .order_by(Friend.account_creation.desc())
        .limit(6)
        )
    results = db.session.scalars(stmt).all()

    data['new'] = [({
        'mii': MiiData().mii_studio_url(user.mii),
        'username': user.username,
        'game': getTitle(user.title_id, titles_to_uid, title_database) if user.online and user.title_id != 0 else '',
        'friendCode': user.friend_code.zfill(12),
        'joinable': user.joinable,
        'network': user.network.lower_name(),
    }) for user in results if user.username]
    data['new'] = data['new'][:2]

    data['num'] = num

    response = make_response(render_template('dist/index.html', data=data))
    return response


# Index page
@app.route('/index.html')
def index_html():
    return index()


# Favicon
@app.route('/favicon.ico')
def favicon():
    return send_file('static/assets/img/favicon.ico')


# Settings page
@app.route('/settings')
def settings():
    if not request.cookies.get('token'):
        return redirect('/connect')
    data = {
        'consoles': [],
    }
    data = sidenav()
    try:
        stmt = (
            select(Discord)
            .where(Discord.site_session_token == request.cookies['token'])
        )
        result = db.session.scalar(stmt)
    except Exception as e:
        if 'invalid token' in str(e):
            response = make_response(redirect('/'))
            response.set_cookie('token', '', expires=0)
            response.set_cookie('user', '', expires=0)
            response.set_cookie('pfp', '', expires=0)
            return response
        return redirect('/')

    data['profileButton'] = result.show_profile_button
    data['smallImage'] = result.show_small_image

    response = make_response(render_template('dist/settings.html', data=data))
    return response


@app.route('/settings.html')
def settings_redirect():
    return redirect('/settings')


# Roster page
@app.route('/roster')
def roster():
    stmt = (
        select(Friend)
        .where(Friend.username != None)
        .order_by(Friend.account_creation.desc())
        .limit(8)
    )
    results = db.session.scalars(stmt).all()

    data = sidenav()

    data['title'] = 'New Users'
    data['users'] = [({
        'mii': MiiData().mii_studio_url(user.mii),
        'username': user.username,
        'game': getTitle(user.title_id, titles_to_uid, title_database),
        'friendCode': user.friend_code.zfill(12),
        'joinable': user.joinable,
        'network': user.network.lower_name(),
    }) for user in results if user.username]

    response = make_response(render_template('dist/users.html', data=data))
    return response


# Active page
@app.route('/active')
def active():
    stmt = (
        select(Friend)
        .where(Friend.username != None)
        .where(Friend.online)
        .order_by(Friend.account_creation.desc())
    )
    results = db.session.scalars(stmt).all()

    data = sidenav()
    data['title'] = 'Active Users'

    data['users'] = [({
        'mii': MiiData().mii_studio_url(user.mii),
        'username': user.username,
        'game': getTitle(user.title_id, titles_to_uid, title_database),
        'friendCode': user.friend_code.zfill(12),
        'joinable': user.joinable,
        'network': user.network.lower_name(),
    }) for user in results if user.username]

    response = make_response(render_template('dist/users.html', data=data))
    return response


# Register page
@app.route('/register.html')
def register():
    network = request.args.get('network')
    if network is None:
        return make_response(render_template('dist/registerselectnetwork.html'))

    try:
        network = NetworkType[network.upper()]
        response = make_response(render_template('dist/register.html', data = {
            'botFC': '-'.join(network.friend_code()[i:i+4] for i in range(0, len(network.friend_code()), 4)),
            'network': network
        }))
        return response
    except:
        return make_response(render_template('dist/registerselectnetwork.html'))


# Register page redirect
@app.route('/register')
def register_page():
    return register()


# Connection page
@app.route('/connect')
def connect():
    return render_template('dist/connect.html', data={'local': local})


@app.route('/discord')
def discord_connect():
    return redirect('/connect')


# Failure page
@app.route('/failure.html')
def failure():
    return render_template('dist/failure.html')


# Success page
@app.route('/success.html')
def success():
    data = {
        'url': 'user/' + request.args.get('fc'),
        'fc': request.args.get('fc'),
        'network': request.args.get('network')
    }
    return render_template('dist/success.html', data=data)


# Consoles page
@app.route('/consoles')
def consoles():
    if not request.cookies.get('token'):
        return redirect('/connect')
    data = {
        'consoles': [],
    }
    try:
        discord_id = user_from_token(request.cookies['token']).id
    except Exception as e:
        if 'invalid token' in str(e):
            response = make_response(redirect('/'))
            response.set_cookie('token', '', expires=0)
            response.set_cookie('user', '', expires=0)
            response.set_cookie('pfp', '', expires=0)
            return response
        return redirect('/')
    for console, active, network_type in get_connected_consoles(discord_id):
        network = NetworkType(network_type)
        stmt = (
            select(Friend)
            .where(Friend.friend_code == console)
            .where(Friend.network == network)
        )
        result = db.session.scalar(stmt)
        data['consoles'].append({
            'fc': '-'.join(console[i:i+4] for i in range(0, 12, 4)),
            'username': result.username,
            'active': active,
            'network': network.lower_name()
        })
    data.update(sidenav())
    response = render_template('dist/consoles.html', data=data)
    return response


@app.route('/user/<string:friend_code>/')
def user_page(friend_code: str):
    network: NetworkType

    try:
        network = nameToNetworkType(request.args.get('network'))

        friend_code_int = int(friend_code.replace('-', ''))
        user_data = get_presence(friend_code_int, network, False)
        if user_data['Exception'] or not user_data['User']['username']:
            raise Exception(user_data['Exception'])
    except:
        return render_template('dist/404.html')

    if not user_data['User']['online'] or not user_data['User']['Presence']:
        user_data['User']['Presence']['game'] = None
    user_data['User']['favoriteGame'] = getTitle(user_data['User']['favoriteGame'], titles_to_uid, title_database)
    user_data['User']['network'] = network.lower_name()
    if user_data['User']['favoriteGame']['name'] == 'Home Screen':
        user_data['User']['favoriteGame'] = None
    for i in ('accountCreation', 'lastAccessed', 'lastOnline'):
        if user_data['User'][i] == 0:
            user_data['User'][i] = 'Never'
        elif time.time() - user_data['User'][i] > 86400:
            user_data['User'][i] = datetime.datetime.fromtimestamp(user_data['User'][i]).strftime('%b %d, %Y')
        elif time.time() - user_data['User'][i] > 600:
            s = str(datetime.timedelta(seconds=int(time.time() - user_data['User'][i]))).split(':')
            user_data['User'][i] = s[0] + 'h, ' + s[1] + 'm, ' + s[2] + 's ago'
        else:
            user_data['User'][i] = 'Just now'
    # COMMENT/DELETE THIS BEFORE COMMITTING
    # print(user_data)
    user_data.update(sidenav())
    response = make_response(render_template('dist/user.html', data = user_data))
    return response


@app.route('/terms')
def terms():
    return redirect('https://github.com/MCMi460/3DS-RPC/blob/main/TERMS.md')

##############
# API ROUTES #
##############


# Create entry in database with friendCode
@app.route('/api/user/create/<int:friend_code>/', methods=['POST'])
@limiter.limit(new_user_limit)
def new_user(friend_code: int, network: int = -1, user_check: bool = True):
    try:
        if user_check:
            user_agent_check()
        if network == -1:
            network = NetworkType.NINTENDO

            try:
                request_arg = request.data.decode('utf-8').split(',')[0]
                network = nameToNetworkType(request_arg)
            except:
                pass            
        create_user(friend_code, network, True)
        return {
            'Exception': False,
        }
    except Exception as e:
        return {
            'Exception': {
                'Error': str(e),
            }
        }


# Grab presence from friendCode
@app.route('/api/user/<int:friend_code>/', methods=['GET'])
@limiter.limit(user_presence_limit)
def user_presence(friend_code: int):
    # Check if a specific network is being specified as a query parameter.
    network_name = request.args.get('network')
    if network_name:
        network = nameToNetworkType(network_name)
    else:
        network = NetworkType.NINTENDO

    return get_presence(friend_code, network, True)


# Toggle
@app.route('/api/toggle/<int:friend_code>/', methods=['POST'])
@limiter.limit(toggler_limit)
def toggler(friend_code: int):
    network = NetworkType.NINTENDO
    if request.data.decode('utf-8').split(',')[2]:
        network = nameToNetworkType(request.data.decode('utf-8').split(',')[2])
    try:
        fc = str(principal_id_to_friend_code(friend_code_to_principal_id(friend_code))).zfill(12)
    except:
        return 'failure!\nthat is not a real friendCode!'
    stmt = (
        select(Friend)
        .where(Friend.friend_code == fc)
        .where(Friend.network == network)
    )
    result = db.session.scalar(stmt)
    if not result:
        return 'failure!\nthat is not an existing friendCode!'
    
    f = request.data.decode('utf-8').split(',')
    token = f[0]
    active = bool(int(f[1]))
    discord_id = user_from_token(token).id
    stmt = (
        select(DiscordFriends)
        .where(DiscordFriends.id == discord_id)
        .where(DiscordFriends.friend_code == fc)
        .where(DiscordFriends.network == network)
    )
    result = db.session.scalar(stmt)

    if not result:
        stmt = select(DiscordFriends).where(DiscordFriends.id == discord_id)
        allFriends = db.session.scalars(stmt).all()
        if len(allFriends) >= 10:
            return 'failure!\nyou can\'t have more than ten consoles added at one time!'
        
    if active:
        db.session.execute(
            update(DiscordFriends)
            .where(DiscordFriends.active)
            .where(DiscordFriends.id == discord_id)
            .values(active = False)
        )
    if result:
        db.session.execute(
            update(DiscordFriends)
            .where(DiscordFriends.friend_code == fc)
            .where(DiscordFriends.network == network)
            .where(DiscordFriends.id == discord_id)
            .values(active=active)
        )
    else:
        db.session.execute(
            insert(DiscordFriends)
            .values(id=discord_id, friend_code=fc, active=active, network=network)
        )
    db.session.commit()
    return 'success!'


# Delete
@app.route('/api/delete/<int:friend_code>/', methods=['POST'])
@limiter.limit(toggler_limit)
def deleter(friend_code: int):
    fc = str(principal_id_to_friend_code(friend_code_to_principal_id(friend_code))).zfill(12)
    if not ',' in request.data.decode('utf-8'): # Old API compatiblity. In the future this should be depercated.
        token = request.data.decode('utf-8')
        discord_id = user_from_token(token).id
        
        db.session.execute(
            delete(DiscordFriends)
            .where(DiscordFriends.friend_code == fc)
            .where(DiscordFriends.network == NetworkType.NINTENDO)
            .where(DiscordFriends.id == discord_id)
        )
        db.session.commit()

        return 'success!'

    data = request.data.decode('utf-8').split(',')
    token = data[0]
    network = nameToNetworkType(data[1])
    discord_id = user_from_token(token).id

    db.session.execute(
            delete(DiscordFriends)
            .where(DiscordFriends.friend_code == fc)
            .where(DiscordFriends.network == network)
            .where(DiscordFriends.id == discord_id)
        )
    db.session.commit()
    return 'success!'


# Toggle one
@app.route('/api/settings/<string:which>/', methods=['POST'])
@limiter.limit(toggler_limit)
def settings_toggler(which: str):
    toggle = bool(int(request.data.decode('utf-8')))
    if not which in ('smallImage', 'profileButton'):
        return 'failure!'
    if which == 'smallImage':
        which = 'show_small_image'
    else:
        which = 'show_profile_button'
    try:
        db.session.execute(
            update(Discord)
            .where(Discord.site_session_token == request.cookies['token'])
            .values({getattr(Discord, which): toggle})
        )

        db.session.commit() 
    except:
        return 'failure!'
    return 'success!'


# Make Nintendo's cert a 'secure' cert
@app.route('/cdn/i/<string:file>/', methods=['GET'])
@limiter.limit(cdn_limit)
def cdn_image(file: str):
    response = make_response(requests.get('https://kanzashi-ctr.cdn.nintendo.net/i/%s' % file, verify=False).content)
    response.headers['Content-Type'] = 'image/jpeg'
    return response


# Local image cache
@app.route('/cdn/l/<string:file>/', methods=['GET'])
@limiter.limit(cdn_limit)
def local_image_cdn(file: str):
    file = hex(int(file, 16)).replace('0x', '').zfill(16).upper()
    return send_file('cache/' + file + '.png')


# Login route
@app.route('/login', methods=['POST'])
@limiter.limit(new_user_limit)
def login():
    try:
        fc = str(principal_id_to_friend_code(friend_code_to_principal_id(request.form['fc']))).zfill(12)
        if request.form['network'] is None:
            network = NetworkType.NINTENDO
        else:
            network = NetworkType(int(request.form['network']))
        new_user(fc, network, False)
    except:
        return redirect('/failure.html')
    return redirect(f'/success.html?fc={fc}&network={network.lower_name()}')


# Discord route
@app.route('/authorize')
@limiter.limit(new_user_limit)
def authorize():
    if not request.args.get('code'):
        return render_template('dist/404.html')
    token, user, pfp = create_discord_user(request.args['code'])
    response = make_response(redirect('/consoles'))
    response.set_cookie('token', token, expires=datetime.datetime.now() + datetime.timedelta(days=30))
    response.set_cookie('user', user, expires=datetime.datetime.now() + datetime.timedelta(days=30))
    response.set_cookie('pfp', pfp, expires=datetime.datetime.now() + datetime.timedelta(days=30))
    return response


@app.route('/refresh')
def refresh():
    if local:
        try:
            token, user, pfp = refresh_bearer(request.cookies['token'])
            response = make_response(redirect('/consoles'))
            response.set_cookie('token', token, expires=datetime.datetime.now() + datetime.timedelta(days=30))
            response.set_cookie('user', user, expires=datetime.datetime.now() + datetime.timedelta(days=30))
            response.set_cookie('pfp', pfp, expires=datetime.datetime.now() + datetime.timedelta(days=30))
            return response
        except:
            delete_discord_user(user_from_token(request.cookies['token']).id)
    return redirect('/404.html')


if __name__ == '__main__':
    cache_titles()
    if local:
        app.run(host='0.0.0.0', port=port)
    else:
        import gevent.pywsgi
        server = gevent.pywsgi.WSGIServer(('0.0.0.0', port), app)
        server.serve_forever()

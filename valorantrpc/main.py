from valorantrpc import webserver,riot_api,utils,oauth,client_api,match_session,unenhanced_match_session
from valorantrpc.exceptions import AuthError
import pypresence,asyncio,json,base64,time,threading,os,subprocess,psutil,ctypes,sys,pystray
from win10toast import ToastNotifier
from pystray import Icon as icon, Menu as menu, MenuItem as item
from PIL import Image, ImageDraw
from dotenv import load_dotenv
from psutil import AccessDenied
import nest_asyncio

# init some modules ya know
nest_asyncio.apply()
load_dotenv()
toaster = ToastNotifier()
current_dir = os.path.dirname(__file__)
favicon = utils.get_resource_path(os.path.join(current_dir,'../data/favicon.ico'))

# variables for main
global systray
systray = None
loop = None
window_shown = False
client_id = None
client_secret = None
client = None
last_presence = {}
session = None
last_state = None
launch_timeout = None
use_enhanced_presence = False
party_invites_enabled = False
config = {} 


default_client_id = "811469787657928704"
current_release = "v2.0b1" #don't forget to update this you bimbo


# ----------------------------------------------------------------------------------------------
# console/taskbar control stuff!
# thanks for some of this pete (github/restrafes) :)
kernel32 = ctypes.WinDLL('kernel32')
user32 = ctypes.WinDLL('user32')
hWnd = kernel32.GetConsoleWindow()

# prevent interaction of the console window which pauses execution
kernel32 = ctypes.windll.kernel32
kernel32.SetConsoleMode(kernel32.GetStdHandle(-10), 128)

# console visibility toggle functionality
def tray_window_toggle(icon, item):
    try:
        global window_shown
        window_shown = not item.checked
        if window_shown:
            user32.ShowWindow(hWnd, 1)
        else:
            user32.ShowWindow(hWnd, 0)
    except:
        pass

def run_systray():
    print("[i] initializing systray object")
    global systray, window_shown

    systray_image = Image.open(favicon)
    systray_menu = menu(
        item('show debug', tray_window_toggle, checked=lambda item: window_shown),
        item('quit', close_program),
    )
    systray = pystray.Icon("valorant-rpc", systray_image, "valorant-rpc", systray_menu)
    systray.run()
    print("[i] systray ready!")

def close_program():
    global systray,client
    user32.ShowWindow(hWnd, 1)
    client.close()
    systray.stop()
    sys.exit()
#end sys tray stuff
# ----------------------------------------------------------------------------------------------


def update_rpc(data):
    if data is None:
        return
    global session,use_enhanced_presence,party_invites_enabled
    if not data["isIdle"]:
        #menu
        if data["sessionLoopState"] == "MENUS" and data["partyState"] != "CUSTOM_GAME_SETUP":
            client.set_activity(
                state=data["party_state"],
                details=("Queue" if data["partyState"] == "MATCHMAKING" else "Lobby") + (f" - {data['queue_id']}" if data["queue_id"] else ""),
                start=data["time"] if not data["time"] == False else None,
                large_image=("game_icon_white" if data["partyState"] == "MATCHMAKING" else "game_icon"),
                large_text="VALORANT",
                small_image="crown_icon" if utils.validate_party_size(data) else None,
                small_text="Party Leader" if utils.validate_party_size(data) else None,
                party_id=data["partyId"],
                party_size=data["party_size"],
                join=data["join_state"] if party_invites_enabled else None
            )

        #custom setup
        elif data["sessionLoopState"] == "MENUS" and data["partyState"] == "CUSTOM_GAME_SETUP":
            game_map = utils.maps[data["matchMap"].split("/")[-1]]
            client.set_activity(
                state=data["party_state"],
                details="Lobby" + (f" - {data['queue_id']}" if data['queue_id'] else ""),
                start=data["time"] if not data["time"] == False else None,
                large_image=f"splash_{game_map.lower()}_square",
                large_text=game_map,
                small_image="crown_icon" if utils.validate_party_size(data) else None,
                small_text="Party Leader" if utils.validate_party_size(data) else None,
                party_id=data["partyId"],
                party_size=data['party_size'],
                join=data['join_state'] if party_invites_enabled else None
            )

        #in da range
        elif data["sessionLoopState"] == "INGAME" and data["provisioningFlow"] == "ShootingRange":
            game_map = utils.maps[data["matchMap"].split("/")[-1]]
            client.set_activity(
                state=data['party_state'],
                details="THE RANGE",
                large_image=f"splash_{game_map.lower()}",
                large_text=game_map,
                small_image=utils.mode_images[data['queue_id'].lower()],
                party_id=data["partyId"],
                party_size=data['party_size'],
                join=data['join_state'] if party_invites_enabled else None
            )

        # if username/password is provided i can get more detailed match info for presence
        if data["sessionLoopState"] == "PREGAME":
            if last_state != "PREGAME":
                # new game session, create match object
                if session is None: 
                    if use_enhanced_presence:
                        session = match_session.Session(client)
                    else:
                        session = unenhanced_match_session.Session(client)
                    session.init_pregame(data)
                    print('new sesh')

        elif data["sessionLoopState"] == "INGAME":
            # if a match doesn't have a pregame
            if last_state != "INGAME":
                if session is None:
                    if use_enhanced_presence:
                        session = match_session.Session(client)
                    else:
                        session = unenhanced_match_session.Session(client)
                    session.init_ingame(data)
            

        


    elif data["isIdle"]:
        client.set_activity(
            state="Away",
            details="Lobby" + (f" - {data['queue_id']}" if data["queue_id"] else ""),
            large_image="game_icon_yellow",
            large_text="VALORANT",
        )


def party_join_listener(data):
    '''
    fires when a party invite (from someone else) has been accepted by the client 
    process the party id and request valorant client api to join party
    '''
    config = utils.get_config()
    username = config['riot-account']['username']
    password = config['riot-account']['password']
    uuid,headers = client_api.get_auth(username,password)
    party_id = data['secret'].split('/')[1]
    print(party_id)
    client_api.post_glz(f'/parties/v1/players/{uuid}/joinparty/{party_id}',headers)
    #somehow this works!


def listen(lockfile):
    '''
    listening loop to check for updates in presence
    '''
    global last_presence,client,session
    while True and utils.is_process_running():
            
        #event listeners
        if party_invites_enabled: 
            client.register_event('ACTIVITY_JOIN',party_join_listener)

        presence = riot_api.get_presence(lockfile)

        # normal listening loop
        if session is None:
            #in the menus, waiting for match
            if presence == last_presence:
                last_presence = presence
                continue
            update_rpc(presence)
            time.sleep(config['settings']['menu_refresh_interval'])

        elif session is not None:
            # match started, now use session object for updating presence
            # while in pregame update less often because less is changing and rate limits
            if presence['sessionLoopState'] != "MENUS":
                session.mainloop(presence)
                time.sleep(config['settings']['ingame_refresh_interval'])
            else:
                del session
                session = None

        last_presence = presence
        last_state = presence['sessionLoopState']

    if not utils.is_process_running():
        close_program()


# ----------------------------------------------------------------------------------------------
# startup
def main(loop):
    '''
    startup routine: load config, start VALORANT, load lockfile, wait for presence
    once startup is complete, run the listening loop
    '''
    global client,client_id,client_secret,config,use_enhanced_presence,party_invites_enabled

    # load config
    config = utils.get_config() 

    launch_timeout = config['settings']['launch_timeout']
    if config['rpc-client-override']['client_id'] != "" and config['rpc-client-override']['client_id'] != default_client_id:
        print("[i] overriding client id!")
        client_id = config['rpc-client-override']['client_id']
    else:
        client_id = default_client_id
    if config['rpc-client-override']['client_secret'] != "":
        print("[i] overriding client secret!")
        client_secret = config['rpc-client-override']['client_secret']
        party_invites_enabled = True
    else:
        party_invites_enabled = False

    if config['riot-account']['username'] != '' and config['riot-account']['password'] != '':
        try:
            uuid,headers = client_api.get_auth(config['riot-account']['username'],config['riot-account']['password'])
            use_enhanced_presence = True
        except AuthError:
            use_enhanced_presence = False
    else:
        print('[i] no riot account detected, using old presence')
        use_enhanced_presence = False
        #figure out if i can still use client.set_activity without whitelisting/oauthing; if so, then just use that

    # setup client
    client = pypresence.Client(int(client_id),loop=loop) 
    webserver.run()
    client.start()

    # authorize app if party invites enabled
    if party_invites_enabled:
        try:
            oauth.authorize(client,client_id,client_secret)
        except:
            use_enhanced_presence = False 
            print('[!] could not authenticate, check the client secret')
    
    launch_timer = 0

    #check for updates
    latest_tag = utils.get_latest_github_release_tag()
    if latest_tag != current_release:
        toaster.show_toast(
            "valorant-rpc update available!",
            f"{current_release} -> {latest_tag}",
            icon_path=favicon,
            duration=10,
            threaded=True
        )
        print(f"[!] an update is available! ({current_release} -> {latest_tag})")

    #check if val is open
    if not utils.is_process_running():
        print("[i] valorant not opened, attempting to run...")
        subprocess.Popen([utils.get_rcs_path(), "--launch-product=valorant", "--launch-patchline=live"])
        while not utils.is_process_running():
            print(f"[i] waiting for valorant... ({launch_timer})",end='\r')
            launch_timer += 1
            if launch_timer >= launch_timeout:
                close_program()
            time.sleep(1)

    #game launching, set loading presence
    client.set_activity(
        state="Loading",
        large_image="game_icon",
        large_text="valorant-rpc by @cm_an#2434",
        buttons=[{
            'label':"View on GitHub",
            'url':"https://github.com/colinhartigan/valorant-rpc"
        }]
    )

    #check for lockfile
    launch_timer = 0
    lockfile = riot_api.get_lockfile()
    if lockfile is None:
        while lockfile is None:
            print(f"[i] waiting for lockfile... ({launch_timer})",end='\r')
            lockfile = riot_api.get_lockfile()
            launch_timer += 1
            if launch_timer >= launch_timeout:
                close_program()
            time.sleep(1)

    #check for presence
    launch_timer = 0
    presence = riot_api.get_presence(lockfile)
    if presence is None:
        while presence is None:
            print(f"[i] waiting for presence... ({launch_timer})",end='\r')
            presence = riot_api.get_presence(lockfile)
            launch_timer += 1
            if launch_timer >= launch_timeout:
                close_program()
            time.sleep(1)

    print("[i] presence detected! hiding window...")
    systray_thread = threading.Thread(target=run_systray)
    systray_thread.start()
    user32.ShowWindow(hWnd, 0)
    time.sleep(1)
    
    print("[i] starting loop")
    update_rpc(presence)
    #print(f"LOCKFILE: {lockfile}")

    #start the loop
    listen(lockfile)


def run():
    loop = asyncio.get_event_loop()
    main(loop)
# ----------------------------------------------------------------------------------------------
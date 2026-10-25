"""Default words for the BranchChat Helper bot (26.10.3).

On first start the server copies these into welcome-bot.json next to config.env. The server owner edits that file,
not this one: the server notices a changed file within a few seconds and uses the new words.
26.10.5: every "where is it" step was checked against the real 26.10.4 screen (the old
"menu at the bottom left" does not exist; the main menu is the three-line button at the top right).
26.10.5: any "how" or reply can be {"phone": ..., "computer": ...}. The app tells the bot which layout the person
sees (phone layout = window 800px wide or less, the same line where the chat list on the left disappears).
If the bot does not know yet, it gives both, labelled "On a phone" and "On a computer".
{name} is replaced with the person's display name (or username). Several replies per topic are picked
in turn at random so the bot does not repeat itself.
"""

DEFAULTS = {
    "_how_to_edit": "Edit the words below and save. Keep the quotes and commas. {name} = the person's name. "
                    "'match' lists the words or phrases that start a reply; 'replies' are picked at random. "
                    "Tour steps run in the order listed; 'auto' steps tick themselves off when the server sees the person do it.",
    "welcome": [
        "Hi {name}, welcome to BranchChat! 🌿 I'm the Helper bot, a friendly guide, not a real person. "
        "I'm here so you're never alone in your chat list, and I can show you around. Want a quick tour?"
    ],
    "welcome_quick": ["Start the tour", "Not now", "What can you do?"],
    "existing_welcome": [
        "Hi {name}! 🌿 I'm the new BranchChat Helper bot (a bot, not a person). I can show you a few things you may not have tried yet. Want a quick tour?"
    ],
    "tour_steps": [
        {"id": "first_message", "auto": True, "ask": "Have you sent your first message yet? Just type anything to me below, like \"hi\".",
         "how": {"phone": "Tap the box at the bottom of this chat, type a message and tap Send. I'll answer right away.",
                 "computer": "Click the box at the bottom of this chat, type a message and press Enter (or click Send). I'll answer right away."},
         "cheer": ["There it is, your first message! 🎉", "Nicely done, {name}. Messages are working."]},
        {"id": "react", "auto": True, "ask": "Have you tried reacting to a message?",
         "how": {"phone": 'Press and hold a message, then pick an emoji. Try it on this message!',
                 "computer": 'Hover over a message and click the little smiley next to it, then pick an emoji. Try it on this message!'},
         "cheer": ["Lovely reaction! 😊", "Reactions work. That's a quick way to say \"I saw it\"."]},
        {"id": "background", "auto": False, "ask": "Have you picked a background for your chats yet?",
         "how": {"phone": 'Open the chat you want to change (this one works too), tap the menu button (three lines) at the top right of this chat, open Colors and Themes, and pick a scene under “This chat’s background”. To use it everywhere, tap “Use this background for all chats”.',
                 "computer": 'Click the chat you want to change in the list on the left (this one works too), click the menu button (three lines) at the top right of the window, open Colors and Themes, and pick a scene under “This chat’s background”. To use it everywhere, click “Use this background for all chats”.'},
         "cheer": ["Good choice! 🌄"]},
        {"id": "theme", "auto": False, "ask": "Have you tried switching between light and dark?",
         "how": {"phone": 'Tap the menu button (three lines) at the top right of this chat and flip the sun / moon switch at the top of the menu. Pick whichever is easier on your eyes.',
                 "computer": 'Click the menu button (three lines) at the top right of the window and flip the sun / moon switch at the top of the menu. Pick whichever is easier on your eyes.'},
         "cheer": ["Looks great either way. ☀️🌙"]},
        {"id": "delete_anim", "auto": True, "ask": "Have you seen a delete animation? They're fun.",
         "how": {"phone": 'Send me a message, then press and hold it and choose Delete. Pick an animation and watch it go.',
                 "computer": 'Send me a message, then hover over it, click the three dots and choose Delete. Pick an animation and watch it go.'},
         "cheer": ["Poof! 💨 Wasn't that fun?"]},
        {"id": "snap", "auto": False, "ask": "Have you peeked at the snap characters yet?",
         "how": "When you delete a message you can pick a character to carry it away. Have a look at each one; they all move a little differently.",
         "cheer": ["Aren't they charming? 🦊"]},
        {"id": "lobby", "auto": True, "ask": "Have you visited the Lobby? It's the shared room everyone on this server can join.",
         "how": {"phone": 'First tap the back arrow at the top left to get to your chat list. Tap Lobby at the top of the list, then Join. Say hello, and please keep it kind; everyone can read it.',
                 "computer": 'Click Lobby at the top of the list on the left, then Join. Say hello, and please keep it kind; everyone can read it.'},
         "cheer": ["Welcome to the Lobby! 👋"]},
        {"id": "directory", "auto": False, "ask": "Have you looked at the directory of groups and servers?",
         "how": "Go to branchchat.app and open the Directory. You can browse the servers listed there and open anything that looks interesting.",
         "cheer": ["Nice exploring! 🧭"]},
        {"id": "friend", "auto": True, "ask": "Have you added a friend or invited someone from your family?",
         "how": {"phone": 'First tap the back arrow at the top left to get to your chat list. Tap Add friend at the bottom of the list. Type their username, or send an invite to their email so they can join.',
                 "computer": 'Click Add friend at the bottom of the list on the left. Type their username, or send an invite to their email so they can join.'},
         "cheer": ["Wonderful, the more the merrier! 🤗"]},
        {"id": "group", "auto": True, "ask": "Have you made a group chat yet?",
         "how": {"phone": 'First tap the back arrow at the top left to get to your chat list. Tap New group at the bottom of the list, give it a name and pick who to add. Groups show up under the Lobby.',
                 "computer": 'Click New group at the bottom of the list on the left, give it a name and pick who to add. Groups show up under the Lobby.'},
         "cheer": ["Your group is ready! 🎊"]},
        {"id": "avatar", "auto": True, "ask": "Have you set a profile picture?",
         "how": {"phone": "First tap the back arrow at the top left to get to your chat list, then tap your name or picture at the very bottom (or tap the menu button (three lines) at the top right of this chat and choose Profile settings). Tap Change profile picture. It helps your family know it's you.",
                 "computer": "Click your name or picture at the bottom left (or Profile settings in the menu button (three lines) at the top right of the window), then click Change profile picture. It helps your family know it's you."},
         "cheer": ["Looking good, {name}! 📸"]},
        {"id": "install", "auto": False, "ask": "Last one: have you installed the BranchChat app?",
         "how": "Open branchchat.app on your phone or computer and tap Download. On a phone you can also choose Add to Home Screen in your browser menu.",
         "cheer": ["All set! 📲"]}
    ],
    "tour_quick": ["Show me how", "Done", "Skip", "Restart tour", "Stop the tour"],
    # 26.10.5: said when someone taps "Restart tour" (or types "start the tour again"); the tour then begins at step one.
    "tour_restarted": ["Starting the tour again from the beginning, {name}! 🌿"],
    "tour_finished": ["That's the whole tour, {name}! 🎉 You've seen everything. I'm still here if you ever have a question; just type \"help\". Want to see it all again? Tap Restart tour."],
    "tour_stopped": ["No problem, I've stopped the tour. If you'd like to pick it up again, just type \"tour\", or \"restart tour\" to start from the beginning. 😊"],
    "tour_not_now": ["That's fine! Whenever you're ready, type \"tour\" and I'll show you around."],
    "tour_next_lead": ["Next up:", "Here's another one:", "Ready for the next one?"],
    "tour_skip": ["Okay, we'll skip that one."],
    "whats_left_intro": ["Here's what's left on the tour:"],
    "whats_left_none": ["You've done the whole tour already! 🎉"],
    "nudge": ["Hi {name}, just checking in. 🌿", "Hope your day is going well, {name}."],
    "tieback": ["By the way,", "Oh, and while I have you:", "When you have a minute:"],
    "fallback": [
        "I'm a simple helper bot, so I didn't quite catch that. 🙂 I can help with: adding friends, groups, the Lobby, backgrounds and themes, delete animations, privacy and safety, installing the app, or getting help. You can also type \"tour\".",
        "Sorry {name}, I'm not sure about that one. Try asking about friends, groups, the Lobby, backgrounds, privacy, the app, or type \"tour\" for a quick walkthrough."
    ],
    "fallback_quick": ["Take the tour", "Add a friend", "Make a group", "Get help"],
    "topics": [
        {"id": "what_is", "match": ["what is branchchat", "what is this", "what's branchchat", "about branchchat", "what is this app", "branchchat"],
         "replies": ["BranchChat is a private chat app made for families and friends. 🌿 Your messages are end-to-end encrypted, so only the people in a chat can read them. You can message friends one-on-one, make group chats, and say hello in the Lobby."]},
        {"id": "add_friend", "match": ["add friend", "add a friend", "invite", "invite family", "find people", "friends", "add someone", "who can i talk to"],
         "replies": [{"phone": "To add a friend, tap the back arrow at the top left to get to your chat list, tap Add friend at the bottom and type their username. If they're not on BranchChat yet, you can invite them by email. Once they accept, they show up in your chats. 🤗",
                 "computer": "To add a friend, click Add friend at the bottom of the list on the left and type their username. If they're not on BranchChat yet, you can invite them by email. Once they accept, they show up in your chats. 🤗"}]},
        {"id": "group", "match": ["group", "make a group", "new group", "group chat", "create group"],
         "replies": [{"phone": 'To make a group, tap the back arrow at the top left to get to your chat list, tap New group at the bottom, give it a name and choose who to add. Your groups appear right under the Lobby.',
                 "computer": 'To make a group, click New group at the bottom of the list on the left, give it a name and choose who to add. Your groups appear right under the Lobby.'}]},
        {"id": "lobby", "match": ["lobby", "public room", "everyone"],
         "replies": [{"phone": "The Lobby is the shared room for everyone on this server. Tap the back arrow at the top left to get to your chat list, then tap Lobby at the top and Join to say hello. Everyone can read it, so keep it kind and don't share private things there. 😊",
                 "computer": "The Lobby is the shared room for everyone on this server. Click Lobby at the top of the list on the left and Join to say hello. Everyone can read it, so keep it kind and don't share private things there. 😊"}]},
        {"id": "backgrounds", "match": ["background", "backgrounds", "theme", "dark mode", "light mode", "colors", "wallpaper", "dark", "light"],
         "replies": [{"phone": 'Open the chat you want to change, tap the menu button (three lines) at the top right of this chat and open Colors and Themes. Pick a scene under “This chat’s background”. The sun / moon switch at the top of that menu changes light and dark. 🌄',
                 "computer": 'Click the chat you want to change in the list on the left, click the menu button (three lines) at the top right of the window and open Colors and Themes. Pick a scene under “This chat’s background”. The sun / moon switch at the top of that menu changes light and dark. 🌄'}]},
        {"id": "delete_anim", "match": ["delete", "animation", "animations", "snap", "characters", "snap characters", "unsend"],
         "replies": [{"phone": 'To delete a message, press and hold it and choose Delete. You can pick a fun animation or a snap character to carry it away. It disappears for everyone in the chat. 💨',
                 "computer": 'To delete a message, hover over it, click the three dots and choose Delete. You can pick a fun animation or a snap character to carry it away. It disappears for everyone in the chat. 💨'}]},
        {"id": "privacy", "match": ["privacy", "safe", "safety", "block", "report", "encrypted", "private", "secure", "stranger", "someone is mean", "bully"],
         "replies": ["Your safety matters. 🛡️ Messages are end-to-end encrypted. If someone bothers you, open their chat and tap the shield button at the top to Report or Block them; a moderator will look at reports. You can also remove a friend at any time: tap their picture at the top of their chat, and Remove friend is at the bottom. If anything ever feels wrong, please tell a parent or someone you trust."]},
        {"id": "install", "match": ["install", "download", "app", "phone", "android", "iphone", "computer", "desktop"],
         "replies": ["You can get the BranchChat app at branchchat.app: tap Download and pick your device. On a phone you can also use your browser's menu and choose Add to Home Screen. 📲"]},
        # 26.10.5: phones show one screen at a time (no list on the left), so switching chats works differently.
        {"id": "switch_chat", "match": ["switch chats", "switch chat", "change chat", "other chat", "go back", "back to my chats", "chat list", "find my chats", "where are my chats", "open another chat"],
         "replies": [{"phone": 'To switch chats, tap the back arrow at the top left of this chat. That takes you to your chat list; then tap the chat you want.',
                 "computer": 'All your chats are in the list on the left. Just click the one you want. (If the list is hidden, click the small › arrow at the left edge to bring it back.)'}]},
        {"id": "help", "match": ["contact", "email", "support", "get help", "need help", "i need help", "talk to a person", "real person", "problem", "bug", "broken", "not working"],
         "replies": ["I'm only a helper bot, but real people can help too. {contact_line} You can also type \"tour\" for a walkthrough."]},
        {"id": "remove_bot", "match": ["remove you", "go away", "leave me alone", "mute you", "delete you", "stop messaging"],
         "replies": ["Of course. I only speak when you write to me (and the occasional tour tip). Type \"stop the tour\" to stop the tips, mute this chat, or remove me (tap my picture or name at the top of this chat, then Remove Helper at the bottom). No hard feelings! 🌿"]}
    ],
    "contact_line_with_email": "You can email {email} and a person will get back to you.",
    "contact_line_without_email": "Ask the person who invited you, or the server's owner, and they can help.",
    "smalltalk": [
        {"id": "serious", "match": ["kill myself", "hurt myself", "want to die", "suicide", "end my life", "someone hurt me", "being abused", "not safe at home", "self harm"],
         "replies": ["I'm really sorry you're feeling this way, {name}. I'm only a bot, but you matter a lot. Please talk to a parent, a trusted adult, or a friend right now. If you're in the US you can call or text 988 any time to talk to someone kind, and if you're in danger please call 911. 💛"],
         "no_tieback": True},
        {"id": "greet_morning", "match": ["good morning", "morning"],
         "replies": ["Good morning, {name}! ☀️ I hope your day is off to a lovely start.", "Morning, {name}! Did you sleep well?"]},
        {"id": "greet_afternoon", "match": ["good afternoon", "afternoon"],
         "replies": ["Good afternoon, {name}! How's your day going?"]},
        {"id": "greet_evening", "match": ["good evening", "evening"],
         "replies": ["Good evening, {name}! 🌇 I hope today was a good one."]},
        {"id": "good_night", "match": ["good night", "goodnight", "night night", "going to bed", "nite"],
         "replies": ["Good night, {name}! Sleep well. 🌙", "Sweet dreams, {name}. God bless, and talk tomorrow! 🌙", "Rest well, {name}. Good night!"], "no_tieback": True},
        {"id": "how_are_you", "match": ["how are you", "how r u", "how are u", "how you doing", "how's it going", "hows it going", "how is it going", "you ok"],
         "replies": ["I'm doing great, thanks for asking, {name}! 😊 How are you?", "I'm well, thank you! Every new message brightens my day. How about you?", "Doing wonderfully. Thanks for asking! How's your day?"]},
        {"id": "whats_up", "match": ["whats up", "what's up", "sup", "wassup", "what are you doing", "what you doing", "wyd"],
         "replies": ["Not much, just here helping people find their way around BranchChat. 🌿 What are you up to?", "Just keeping the chat warm for you, {name}! What's new with you?"]},
        {"id": "hello", "match": ["hello", "hi", "hey", "hiya", "howdy", "greetings", "yo"],
         "replies": ["Hi {name}! 👋", "Hello, {name}! Nice to hear from you.", "Hey {name}! 😊 What can I help you with?"]},
        {"id": "im_good", "match": ["im good", "i'm good", "im fine", "i'm fine", "doing well", "im great", "i'm great", "pretty good", "not bad"],
         "replies": ["I'm so glad to hear that! 😊", "Wonderful, {name}!"]},
        {"id": "sad", "match": ["sad", "upset", "lonely", "bad day", "not good", "crying", "depressed", "feel bad", "unhappy"],
         "replies": ["I'm sorry you're feeling down, {name}. 💛 It can really help to talk to someone you trust, like a parent or a good friend. You could message them right here. I'm thinking of you.",
                     "That sounds hard, {name}. You're not alone. Reaching out to someone who loves you can make a big difference. Sending you a little sunshine. ☀️"], "no_tieback": True},
        {"id": "bored", "match": ["bored", "boring", "nothing to do"],
         "replies": ["Bored? Let's fix that! You could try a delete animation, change your background, or say hi in the Lobby. 🎈", "How about making a group with your family and sharing a photo from today? 📸"]},
        {"id": "thanks", "match": ["thanks", "thank you", "thx", "ty", "appreciate it", "thank u"],
         "replies": ["You're very welcome, {name}! 😊", "Happy to help!", "Anytime! 🌿"]},
        {"id": "bye", "match": ["bye", "goodbye", "see you", "see ya", "talk later", "later", "cya", "gotta go"],
         "replies": ["Bye, {name}! Have a wonderful day. 👋", "See you soon, {name}! Take care."], "no_tieback": True},
        {"id": "are_you_bot", "match": ["are you a bot", "are you real", "are you human", "are you a person", "is this a bot", "are you ai", "robot", "bot"],
         "replies": ["Yes, I'm a helper bot, not a real person. 🤖 I answer with messages people wrote ahead of time, so I'm simple, but I'm always here to help you find your way around."]},
        {"id": "who_made", "match": ["who made you", "who created you", "who built you", "who are you", "your name", "what are you"],
         "replies": ["I'm the BranchChat Helper bot. The BranchChat team made me to welcome people and show them around. 🌿"]},
        {"id": "joke", "match": ["joke", "tell me a joke", "make me laugh", "something funny", "funny"],
         "replies": ["Why did the tree get on the computer? To log in! 🌳", "What do you call a bear with no teeth? A gummy bear! 🐻",
                     "Why don't eggs tell jokes? They'd crack each other up! 🥚", "What did one wall say to the other? I'll meet you at the corner!",
                     "Why was the math book sad? It had too many problems. 📚", "What kind of tree fits in your hand? A palm tree! 🌴"]},
        {"id": "compliment", "match": ["you're nice", "youre nice", "you are nice", "good bot", "i like you", "you're cool", "youre cool", "love you", "you're awesome", "youre awesome", "you're great", "smart"],
         "replies": ["Aw, thank you, {name}! That made my day. 😊", "You're very kind! I think you're pretty great too."]},
        {"id": "yes", "match": ["yes", "yeah", "yep", "sure", "ok", "okay", "yup", "of course", "please"],
         "replies": ["Great! 😊"]},
        {"id": "no", "match": ["no", "nope", "nah", "no thanks", "not really"],
         "replies": ["That's okay! Just let me know if you need anything."]}
    ]
}



# 26.10.5: servers that already copied the older words into welcome-bot.json keep the wrong "menu at the bottom
# left" directions. When the bot reads that file, any text that still matches an old default word for word is
# swapped for the corrected one (in memory only; anything the server owner rewrote is left alone).
OLD_WRONG_TEXT = {
    "Tap the box at the bottom of this chat, type a message and press Send (or Enter). I'll answer right away.":
        {'phone': "Tap the box at the bottom of this chat, type a message and tap Send. I'll answer right away.", 'computer': "Click the box at the bottom of this chat, type a message and press Enter (or click Send). I'll answer right away."},
    'Press and hold a message (or hover and click the little smiley on a computer), then pick an emoji. Try it on this message!':
        {'phone': 'Press and hold a message, then pick an emoji. Try it on this message!', 'computer': 'Hover over a message and click the little smiley next to it, then pick an emoji. Try it on this message!'},
    "Open the menu at the bottom left (your name), choose Backgrounds, and tap one you like. They're all scenes from nature.":
        {'phone': 'Open the chat you want to change (this one works too), tap the menu button (three lines) at the top right of this chat, open Colors and Themes, and pick a scene under “This chat’s background”. To use it everywhere, tap “Use this background for all chats”.', 'computer': 'Click the chat you want to change in the list on the left (this one works too), click the menu button (three lines) at the top right of the window, open Colors and Themes, and pick a scene under “This chat’s background”. To use it everywhere, click “Use this background for all chats”.'},
    'Open the menu at the bottom left and tap the Light / Dark switch. Pick whichever is easier on your eyes.':
        {'phone': 'Tap the menu button (three lines) at the top right of this chat and flip the sun / moon switch at the top of the menu. Pick whichever is easier on your eyes.', 'computer': 'Click the menu button (three lines) at the top right of the window and flip the sun / moon switch at the top of the menu. Pick whichever is easier on your eyes.'},
    'Send me a message, then press and hold it (or click the three dots) and choose Delete. Pick an animation and watch it go.':
        {'phone': 'Send me a message, then press and hold it and choose Delete. Pick an animation and watch it go.', 'computer': 'Send me a message, then hover over it, click the three dots and choose Delete. Pick an animation and watch it go.'},
    'Tap Lobby at the top of your conversation list, then Join. Say hello, and please keep it kind; everyone can read it.':
        {'phone': 'First tap the back arrow at the top left to get to your chat list. Tap Lobby at the top of the list, then Join. Say hello, and please keep it kind; everyone can read it.', 'computer': 'Click Lobby at the top of the list on the left, then Join. Say hello, and please keep it kind; everyone can read it.'},
    "Open the menu at the bottom left and choose Directory. You can browse what's there and open anything that looks interesting.":
        'Go to branchchat.app and open the Directory. You can browse the servers listed there and open anything that looks interesting.',
    'Tap Add friend at the bottom of your conversation list. Type their username, or send an invite to their email so they can join.':
        {'phone': 'First tap the back arrow at the top left to get to your chat list. Tap Add friend at the bottom of the list. Type their username, or send an invite to their email so they can join.', 'computer': 'Click Add friend at the bottom of the list on the left. Type their username, or send an invite to their email so they can join.'},
    'Tap New group at the bottom of your conversation list, give it a name and pick who to add. Groups show up under the Lobby.':
        {'phone': 'First tap the back arrow at the top left to get to your chat list. Tap New group at the bottom of the list, give it a name and pick who to add. Groups show up under the Lobby.', 'computer': 'Click New group at the bottom of the list on the left, give it a name and pick who to add. Groups show up under the Lobby.'},
    "Open the menu at the bottom left, tap your picture or Profile, and choose a photo. It helps your family know it's you.":
        {'phone': "First tap the back arrow at the top left to get to your chat list, then tap your name or picture at the very bottom (or tap the menu button (three lines) at the top right of this chat and choose Profile settings). Tap Change profile picture. It helps your family know it's you.", 'computer': "Click your name or picture at the bottom left (or Profile settings in the menu button (three lines) at the top right of the window), then click Change profile picture. It helps your family know it's you."},
    'That\'s the whole tour, {name}! 🎉 You\'ve seen everything. I\'m still here if you ever have a question; just type "help".':
        'That\'s the whole tour, {name}! 🎉 You\'ve seen everything. I\'m still here if you ever have a question; just type "help". Want to see it all again? Tap Restart tour.',
    'No problem, I\'ve stopped the tour. If you\'d like to pick it up again, just type "tour". 😊':
        'No problem, I\'ve stopped the tour. If you\'d like to pick it up again, just type "tour", or "restart tour" to start from the beginning. 😊',
    "To add a friend, tap Add friend at the bottom of your conversation list and type their username. If they're not on BranchChat yet, you can invite them by email. Once they accept, they show up in your chats. 🤗":
        {'phone': "To add a friend, tap the back arrow at the top left to get to your chat list, tap Add friend at the bottom and type their username. If they're not on BranchChat yet, you can invite them by email. Once they accept, they show up in your chats. 🤗", 'computer': "To add a friend, click Add friend at the bottom of the list on the left and type their username. If they're not on BranchChat yet, you can invite them by email. Once they accept, they show up in your chats. 🤗"},
    'To make a group, tap New group at the bottom of your conversation list, give it a name and choose who to add. Your groups appear right under the Lobby.':
        {'phone': 'To make a group, tap the back arrow at the top left to get to your chat list, tap New group at the bottom, give it a name and choose who to add. Your groups appear right under the Lobby.', 'computer': 'To make a group, click New group at the bottom of the list on the left, give it a name and choose who to add. Your groups appear right under the Lobby.'},
    "The Lobby is the shared room for everyone on this server. Tap Lobby at the top of your chats and Join to say hello. Everyone can read it, so keep it kind and don't share private things there. 😊":
        {'phone': "The Lobby is the shared room for everyone on this server. Tap the back arrow at the top left to get to your chat list, then tap Lobby at the top and Join to say hello. Everyone can read it, so keep it kind and don't share private things there. 😊", 'computer': "The Lobby is the shared room for everyone on this server. Click Lobby at the top of the list on the left and Join to say hello. Everyone can read it, so keep it kind and don't share private things there. 😊"},
    "Open the menu at the bottom left (your name). There you'll find Backgrounds, with nature scenes to choose from, and the Light / Dark switch. 🌄":
        {'phone': 'Open the chat you want to change, tap the menu button (three lines) at the top right of this chat and open Colors and Themes. Pick a scene under “This chat’s background”. The sun / moon switch at the top of that menu changes light and dark. 🌄', 'computer': 'Click the chat you want to change in the list on the left, click the menu button (three lines) at the top right of the window and open Colors and Themes. Pick a scene under “This chat’s background”. The sun / moon switch at the top of that menu changes light and dark. 🌄'},
    'To delete a message, press and hold it (or click the three dots) and choose Delete. You can pick a fun animation or a snap character to carry it away. It disappears for everyone in the chat. 💨':
        {'phone': 'To delete a message, press and hold it and choose Delete. You can pick a fun animation or a snap character to carry it away. It disappears for everyone in the chat. 💨', 'computer': 'To delete a message, hover over it, click the three dots and choose Delete. You can pick a fun animation or a snap character to carry it away. It disappears for everyone in the chat. 💨'},
    'Your safety matters. 🛡️ Messages are end-to-end encrypted. If someone bothers you, open their chat, tap their name and choose Block or Report; a moderator will look at reports. You can also remove a friend at any time. If anything ever feels wrong, please tell a parent or someone you trust.':
        'Your safety matters. 🛡️ Messages are end-to-end encrypted. If someone bothers you, open their chat and tap the shield button at the top to Report or Block them; a moderator will look at reports. You can also remove a friend at any time: tap their picture at the top of their chat, and Remove friend is at the bottom. If anything ever feels wrong, please tell a parent or someone you trust.',
    'Of course. I only speak when you write to me (and the occasional tour tip). Type "stop the tour" to stop the tips, mute this chat, or remove me like any friend from my chat\'s menu. No hard feelings! 🌿':
        'Of course. I only speak when you write to me (and the occasional tour tip). Type "stop the tour" to stop the tips, mute this chat, or remove me (tap my picture or name at the top of this chat, then Remove Helper at the bottom). No hard feelings! 🌿',
    'Of course. I only speak when you write to me (and the occasional tour tip). Type "stop the tour" to stop the tips, mute this chat, or remove me like any friend (tap the info gear at the top of this chat, then Remove friend at the bottom). No hard feelings! 🌿':
        'Of course. I only speak when you write to me (and the occasional tour tip). Type "stop the tour" to stop the tips, mute this chat, or remove me (tap my picture or name at the top of this chat, then Remove Helper at the bottom). No hard feelings! 🌿',
    'Of course. I only speak when you write to me (and the occasional tour tip). Type "stop the tour" to stop the tips, mute this chat, or remove me (tap the info gear or my name at the top of this chat, then Remove Helper at the bottom). No hard feelings! 🌿':
        'Of course. I only speak when you write to me (and the occasional tour tip). Type "stop the tour" to stop the tips, mute this chat, or remove me (tap my picture or name at the top of this chat, then Remove Helper at the bottom). No hard feelings! 🌿',
    'Of course. I only speak when you write to me (and the occasional tour tip). Type "stop the tour" to stop the tips, mute this chat, or remove me like any friend (tap my picture at the top of this chat, then Remove friend at the bottom). No hard feelings! 🌿':
        'Of course. I only speak when you write to me (and the occasional tour tip). Type "stop the tour" to stop the tips, mute this chat, or remove me (tap my picture or name at the top of this chat, then Remove Helper at the bottom). No hard feelings! 🌿',
}

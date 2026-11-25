ca.pem         public — give to edge clients
ca_key.pem     SECRET — CA signing key, never in image
server.pem     public — presented by ThingsBoard
server_key.pem SECRET — server private key, never in image

Imagine you want to send secret walkie-talkie messages to your best friend, ThingsBoard, and you want to be 100% sure a sneaky stranger isn't pretending to be ThingsBoard.

Here is how the 4 files work, like a superhero club:

    ca_key.pem (The Magic Wax Stamp — kept locked in a box): This is the club boss's secret stamp. Only the boss has it. Nobody ever gives it away or sends it in the mail.

    ca.pem (A Picture of the Magic Stamp — given to Python): You get a picture of what the real club stamp looks like, so you can spot a real badge from a fake one.

    server.pem (ThingsBoard's Club Badge — given to ThingsBoard): This is a shiny ID card for ThingsBoard. It has ThingsBoard's nicknames written on it (localhost, thingsboard-ce, 127.0.0.1) and has the Magic Wax Stamp pressed onto the bottom.

    server_key.pem (ThingsBoard's Secret Handshake — kept secret by ThingsBoard): Only the real ThingsBoard knows this secret handshake. ThingsBoard never tells it to anyone.

What Happens When You Call ThingsBoard

    You knock: Python calls ThingsBoard and says, "Hi! Show me your badge!"

    ThingsBoard shows the badge: ThingsBoard sends server.pem (the badge—never the secret handshake!).

    You check the stamp: Python holds up ca.pem (the picture of the stamp) next to the badge. "Yep! That's the real Magic Stamp!"

    You check the nickname: Python checks the badge to make sure the name you called (localhost or thingsboard-ce) is actually written on it.

    You test the secret handshake: Python asks ThingsBoard to do a quick secret-handshake puzzle. ThingsBoard uses server_key.pem in its head to solve it, proving it didn't just find someone else's badge on the ground.

    You talk in secret code: Now that you know it's really ThingsBoard, you both lock the door and start sending encrypted messages!

# Inter-machine Clipboard (IMC)

A server and several clients to allow copying content on one device and pasting it on other devices. Currently, there are clients for iPhones and Windows PCs. 

The iPhone client is actually split into 3 iOS Shortcuts, downloadable from these links:
- [Send to IMC](https://www.icloud.com/shortcuts/356b7c31aaa04127b02f228a1a66f33a)
- [Get from IMC](https://www.icloud.com/shortcuts/ed790dc4d9ac4c168e10668a7702a1d6)
- [Save Clipboard to Photos](https://www.icloud.com/shortcuts/63e55ce69da443cf9c7bb15a01771235)

On iPhone, you must copy content the normal way first, and then run the Send to IMC shortcut. Or, tap "Share" on your content and select Send to IMC from the Share Sheet. Similarly, you must run the Get from IMC shortcut to download content from the IMC server to your iPhone's clipboard, and then paste that content the normal way. The Save Clipboard to Photos shortcut allows you to do just that.

On Windows, whatever you copy is automatically sent to the IMC server, and whatever the IMC server receives from other devices is automatically placed on your Windows PC's clipboard.

## How to run
1. Run `python server.py` on the machine you want to be the IMC server. Do this from the same directory `server.py` is in. 
2. Wait until the log shows a line containing `wsgi starting up on http://[ip address]:[port]`. The server is now ready.
3. Run `python windows_server.py` on your Windows PC. Now you're good to go.

## Bugs 
- Currently, the server and clients send data back and forth in ***PLAIN, UNENCRYPTED HTTP***, so **USE WITH CAUTION!!!**
- Has only been tested while running on 1 iPhone and 1 Windows PC. There may be race conditions when running more clients at the same time.
- Copying a photo to the iPhone's clipboard and then running Send to IMC sends the photo without a file extension, so after pasting it on your Windows PC, you must manually add one to see the photo's thumbnail.
- Tapping "Share" in Safari on the iPhone, and then selecting Send to IMC from the Share Sheet, will send the HTML contents of the webpage instead of the page's URL.

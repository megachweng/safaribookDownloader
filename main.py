from safariBookQueue import *
email = 'email@email.com'
passwd = 'password'
# proxies = dict(http='socks5://127.0.0.1:1086',
#                https='socks5://127.0.0.1:1086')
proxies = None
session = login(email, passwd)
downloader = Downloader(session)
downloader.getBook(idJson=downloader.getBookIDs())
Writer(logedSession=session, proxies=proxies).start()

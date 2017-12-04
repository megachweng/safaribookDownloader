from safariBookQueue import *
email = 'username@email.com'
passwd = 'password'

session = login(email, passwd)
downloader = Downloader(session)
downloader.getBook(idJson=downloader.getBookIDs())
Writer(logedSession=session).start()

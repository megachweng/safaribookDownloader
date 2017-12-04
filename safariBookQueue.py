# -*- coding: utf-8 -*-import os
import re
import sys
import json
import math
import time
import pyprind
import grequests
import requests
import os
import pathlib
import lxml
import html
import jinja2
import shutil
from lxml import etree
from bs4 import BeautifulSoup
from zipfile import ZipFile
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry


def login(username, password):
    print('Authenticating SafariBookOnline...', end='')
    sys.stdout.flush()
    URL = 'https://www.safaribooksonline.com/accounts/login/'
    session = requests.session()
    session.get(URL)
    if 'csrfsafari' in session.cookies:
        csrftoken = session.cookies['csrfsafari']

    login_form = {
        'csrfmiddlewaretoken': csrftoken,
        'email': username,
        'password1': password,
        'login': 'Sign In',
        'next': '',
    }

    resp = session.post(URL, data=login_form, headers=dict(Referer=URL))
    if 'The password and email address did not match' in resp.text:
        print('\nLogin failed! The password and email address did not match!')
        exit()
    else:
        print('success!')
        session.mount('http://', HTTPAdapter(max_retries=30))
        session.mount('https://', HTTPAdapter(max_retries=30))
        return session


class Downloader:
    def __init__(self, session, proxies=None, timeout=5):
        self.timeout = timeout
        self.proxies = proxies
        self.webUrl = None
        self.downloadPath = None
        self.imageBar = None
        self.chapterBar = None
        self.cssBar = None
        self.bookIDs = None
        self.session = session
        self.baseAPI = "https://www.safaribooksonline.com/api/v1/book/"

    def getBookIDs(self):
        def __appendID(raw, *args, **kwargs):
            nonlocal bookIDs
            bookIDs += json.loads(raw.text)['titles']
        print('Geting your books in queue......', end='')
        sys.stdout.flush()
        url = "https://www.safaribooksonline.com/api/v1/dashboard/in_your_queue/?start="
        results = json.loads(self.session.get(
            url + '0', timeout=self.timeout).text)
        bookIDs = results['titles']  # save first 10 book ids
        total = results['total']
        steps = [url + str(i) for i in range(10, total, 10)]
        bookIDsTask = [(grequests.get(step, session=self.session, timeout=self.timeout,
                                      proxies=self.proxies, hooks=dict(response=__appendID))) for step in steps]
        grequests.map(bookIDsTask, size=20)
        bookIDs = sorted(bookIDs, key=lambda k: k['title'])
        processedIDs = [{'title': item['title'], 'id':item['identifier'], 'issued':item['issued']}
                        for item in list(filter(lambda x:x['format'] == 'book', bookIDs))]
        with open('bookIDs.json', 'w') as f:
            f.write(json.dumps(processedIDs, indent=4))
        print('Done\nYour books ID has been saved at \'bookID.json\'')
        return processedIDs

    def getBook(self, idJsonFile=False, idJson=False):
        if idJson:
            bookIDs = idJson
        else:
            with open(idJsonFile, 'r') as idf:
                bookIDs = json.load(idf)
        for index, bookID in enumerate(bookIDs):
            total = len(bookIDs)
            print(
                '------------------{}/{}------------------'.format(str(index + 1), str(total)))
            print("Title: {}[{}]".format(bookID['title'], bookID['id']))
            print("Geting META.....................", end='')
            sys.stdout.flush()
            response = self.session.get(
                self.baseAPI + bookID['id'], timeout=self.timeout, proxies=self.proxies)
            bookJson = json.loads(response.text)

            if 'detail' in bookJson.keys():
                if 'have permission' in bookJson['detail']:
                    print('You Account has been suspended')
                    exit()

            self.webUrl = bookJson['web_url']
            self.basePath = os.path.join('Downloaded', re.sub(
                '[^\w\-_\. ]', '_', re.sub('\s+', '_', bookJson['title'])))
            self.downloadPath = os.path.join(self.basePath, 'OEBPS')
            pathlib.Path(os.path.join(self.basePath, "dev")
                         ).mkdir(parents=True, exist_ok=True)
            pathlib.Path(self.downloadPath).mkdir(parents=True, exist_ok=True)

            self.META = {
                'title': bookJson['title'],
                'language': bookJson['language'],
                'creator': ','.join([i['name'] for i in bookJson['authors']]),
                'publisher': ','.join([i['name'] for i in bookJson['publishers']]),
                'rights': bookJson['rights'],
                'isbn': bookJson['isbn'],
                'issued': bookJson['issued'],
                'modified': bookJson['updated'],
                'baseAPI': bookJson['url']
            }

            print('Done')
            self.getTOC(bookID['id'])
            self.tasks = self.__prepareTasks(bookID['id'])
            self.__getAllChaptersContent()
            self.__getAllChaptersImages()
            self.__getAllCSS()
            self.__getCover(bookID['id'])

            with open(os.path.join(self.basePath, 'dev', 'META.json'), 'w') as f:
                f.write(json.dumps(self.META, indent=4))
            with open(os.path.join(self.basePath, 'dev', 'TASKS.json'), 'w') as f:
                f.write(json.dumps(self.tasks, indent=4))
            print('------------------Done------------------\n')

    def getTOC(self, bookID):
        print('Geting Table of Content.........', end='')
        sys.stdout.flush()
        rs = self.session.get(self.baseAPI + bookID + "/toc",
                              timeout=self.timeout, proxies=self.proxies)
        with open(os.path.join(self.basePath, 'dev', 'TOC.json'), 'w') as f:
            f.write(json.dumps(json.loads(rs.text.encode(
                "utf-8").decode('utf-8')), indent=4))
        print('Done')

    def __prepareTasks(self, bookID):
        print('Iterating chapters..............', end='')
        sys.stdout.flush()
        url = "https://www.safaribooksonline.com/api/v1/book/" + bookID + "/chapter/?page="
        toc = list()
        cssFiles = set()
        imageFiles = set()
        high_resolution_cover = False
        pageNumber = 0
        while True:
            pageNumber += 1
            response = self.session.get(
                url + str(pageNumber), timeout=self.timeout, proxies=self.proxies)
            jsonChapters = json.loads(response.text)
            for chapter in jsonChapters['results']:
                if (chapter['filename'].lower() in ['cover.html', 'cover.xhtml']) and chapter['images']:
                    high_resolution_cover = chapter['images'][0]
                toc.append({'head_extra': chapter['head_extra'], 'title': chapter['title'],
                            'content': chapter['content'], 'filename': chapter['filename']})
                for item in chapter['images']:
                    imageFiles.add(item)
                for item in chapter['stylesheets']:
                    cssFiles.add(item['original_url'])
            if jsonChapters['next'] is None:
                break

        tasks = {'TOC': toc, 'images': list(imageFiles), 'css': list(cssFiles)}
        self.META['cover'] = high_resolution_cover
        with open(os.path.join(self.basePath, 'dev', 'TASKS.json'), 'w') as f:
            f.write(json.dumps(tasks, indent=4))
        if not high_resolution_cover:
            with open('lr_cover.log', 'a') as lrlog:
                lrlog.write(self.META['title'] + "\n")
        print('Done')
        return tasks

    def __getAllChaptersContent(self):
        TOC = self.tasks['TOC']
        sys.stdout.flush()
        self.chapterBar = pyprind.ProgBar(
            len(TOC), track_time=False, bar_char='-', title='\nGeting Chapers content')
        chaptersTask = [(grequests.get(chapter['content'], timeout=self.timeout, session=self.session,
                                       proxies=self.proxies, hooks=dict(response=self.__saveFiles))) for chapter in TOC]
        grequests.map(chaptersTask, size=100)

    def __getAllChaptersImages(self):
        pathlib.Path(self.downloadPath +
                     '/images').mkdir(parents=True, exist_ok=True)
        imagesFileName = self.tasks['images']
        self.imageBar = pyprind.ProgBar(
            len(imagesFileName), track_time=False, bar_char='-', title='\nGeting images')
        imagesTask = [grequests.get(self.webUrl + filename, timeout=self.timeout, session=self.session,
                                    proxies=self.proxies, hooks=dict(response=self.__saveFiles)) for filename in imagesFileName]
        grequests.map(imagesTask, size=100)

    def __getAllCSS(self):
        pathlib.Path(self.downloadPath +
                     '/styles').mkdir(parents=True, exist_ok=True)
        cssFileName = self.tasks['css']
        self.cssBar = pyprind.ProgBar(
            100 * len(cssFileName), track_time=False, bar_char='-', title='\nGeting StyleSheets')
        cssTask = [grequests.get(url, timeout=self.timeout, proxies=self.proxies, session=self.session, hooks=dict(
            response=self.__saveFiles)) for url in cssFileName]
        grequests.map(cssTask, size=2)

    def __getCover(self, bookID):
        if not self.META['cover']:
            print('Low resolution cover')
            rs = self.session.get('https://www.safaribooksonline.com/library/cover/' +
                                  bookID, timeout=self.timeout, proxies=self.proxies)
            with open(os.path.join(self.downloadPath, 'images', 'lr_cover.jpg'), 'wb') as f:
                f.write(rs.content)
            self.META['cover'] = 'lr_cover.jpg'
        else:
            print('High resolution cover')
            self.META['cover'] = self.META['cover'].rsplit('/')[-1]

    def __saveFiles(self, raw, *args, **kwargs):
        filename = raw.url.rsplit('/')[-1]
        if 'image' in raw.headers['content-type']:
            with open(os.path.join(self.downloadPath, 'images', filename), 'wb') as f:
                self.imageBar.update()
                f.write(raw.content)

        elif 'css' in raw.headers['content-type']:
            with open(os.path.join(self.downloadPath, 'css', filename), 'w') as f:
                self.cssBar.update(iterations=100)
                f.write(raw.text)
        else:
            tree = etree.HTML(raw.text)
            imgs = tree.xpath('.//img')
            if imgs:
                for img in imgs:
                    img.set('src', 'images/' + img.get('src').rsplit('/')[-1])

            with open(os.path.join(self.downloadPath, filename), 'w') as f:
                f.write(etree.tostring(tree).decode('utf-8'))
                self.chapterBar.update()


class Writer:
    def __init__(self, input_path="Downloaded", output_path="Output", logedSession=None):
        pathlib.Path(output_path).mkdir(parents=True, exist_ok=True)
        self.tasks = [dir for dir in os.listdir(
            input_path) if not dir.startswith(".")]
        self.progress = pyprind.ProgBar(
            len(self.tasks), track_time=False, bar_char='-', title='\nConverting to ePUB')
        self.input_path = input_path
        self.output_path = output_path
        self.loadTemplate()
        self.session = logedSession

    def loadTemplate(self):
        templateLoader = jinja2.FileSystemLoader(searchpath="template")
        templateEnv = jinja2.Environment(loader=templateLoader)

        self.chapterTPL = templateEnv.get_template('chapter.html')
        self.tocTPL = templateEnv.get_template('toc.html')
        self.OPFTPL = templateEnv.get_template('content.html')

    def __rendChapter(self, chapterFile, title, css, head_extra):
        try:
            with open(chapterFile, encoding="utf-8") as fp:
                soup = BeautifulSoup(fp, "lxml")
        except FileNotFoundError as notFoundFile:
            tmp = pathlib.Path(notFoundFile.filename)
            _bookPath = tmp.parents[1]
            retrivingFile = tmp.name
            print('Missing file:', retrivingFile)
            print('Retriving...')
            api = json.load(open(os.path.join(_bookPath, 'dev', 'META.json')))[
                'baseAPI']
            res = self.session.get(
                api + "chapter-content/" + retrivingFile, timeout=10)
            soup = BeautifulSoup(res.text.encode('utf-8'), 'lxml')

        outputChapter = self.chapterTPL.render({
            'title': title,
            'css': css,
            'head_extra': head_extra,
            'body': soup.body
        })
        with open(chapterFile, 'w', encoding="utf-8") as f:
            f.write(outputChapter)

    def __rendTOC(self, bookContentPath, title, toc):
        outputToc = self.tocTPL.render({'title': title, 'TOC': toc})
        with open(os.path.join(bookContentPath, "toc.ncx"), 'w', encoding="utf-8") as f:
            f.write(outputToc)

    def __rendOPF(self, bookContentPath, chapters='null', images='null', isbn='null', language='null', publisher='null', title='null', authors='null', css='null', cover='null'):
        outputopf = self.OPFTPL.render({
            'chapters': chapters,  # [{xxx:xxx, filename:xxx}]
            'images': images,
            'BookID': isbn,
            'publisher': publisher,
            'title': title,
            'creator': authors,
            'cover': cover,
            'css': css
        })
        with open(os.path.join(bookContentPath, "content.opf"), 'w', encoding="utf-8") as f:
            f.write(outputopf)

    def __others(self, bookPath, bookContentPath):
        pathlib.Path(os.path.join(bookPath, 'META-INF')
                     ).mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(os.path.join('template', '_website.css'), os.path.join(
                bookContentPath, 'styles', '_website.css'))
            shutil.copyfile(os.path.join('template', 'container.xml'), os.path.join(
                bookPath, 'META-INF', 'container.xml'))
            shutil.copyfile(os.path.join('template', 'mimetype'),
                            os.path.join(bookPath, 'mimetype'))
        except:
            pass

    def __createBook(self, bookName, bookPath, outputPath='Output'):
        epubfile = ZipFile(os.path.join(outputPath, bookName + '.epub'), 'w')
        retval = os.getcwd()
        os.chdir(bookPath)
        for dirname, subdirs, files in os.walk('.'):
            if 'dev' in subdirs:
                subdirs.remove('dev')
            for file in files:
                epubfile.write(os.path.join(dirname, file))
        epubfile.close()
        os.chdir(retval)

    def start(self):
        for bookName in self.tasks:
            self.progress.update()
            bookContentPath = os.path.join(self.input_path, bookName, 'OEBPS')
            bookPath = os.path.join(self.input_path, bookName)

            toc = json.load(open(os.path.join(self.input_path,
                                              bookName, "dev", "TOC.json"), encoding="utf-8"))
            chapters = json.load(open(os.path.join(
                self.input_path, bookName, "dev", "TASKS.json"), encoding="utf-8"))
            meta = json.load(open(os.path.join(
                self.input_path, bookName, "dev", "META.json"), encoding="utf-8"))

            self.__rendTOC(bookContentPath, meta['title'], toc)

            css = [cssurl.rsplit('/')[-1] for cssurl in chapters['css']]
            self.__rendOPF(bookContentPath, chapters['TOC'], chapters['images'], meta['isbn'],
                           meta['language'], meta['publisher'], meta['title'], meta['creator'], css, meta['cover'])
            self.__others(bookPath, bookContentPath)
            for chapter in chapters['TOC']:
                self.__rendChapter(os.path.join(
                    bookContentPath, chapter['filename']), meta['title'], css, chapter['head_extra'])

            self.__createBook(bookName, bookPath)

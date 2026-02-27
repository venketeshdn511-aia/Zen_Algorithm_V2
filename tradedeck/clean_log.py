import codecs
try:
    with codecs.open('test_api6.log', 'r', 'utf-16le') as fin:
        with codecs.open('test_api6_clean.txt', 'w', 'utf-8') as fout:
            fout.write(fin.read())
except Exception as e:
    print(e)

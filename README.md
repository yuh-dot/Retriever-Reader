# AI王第3回FiD-baselineの使い方
目次
- 文集合の作成
- 位置情報の埋め込み

## 文集合の作成
### 流れ
wikipediaダンプファイルの取得→文ごとにslice→Elasticsearchの起動→Elasticsearch インデックスの作成→文集合作成

### wikipediaダンプファイルの取得
[https://github.com/cl-tohoku/quiz-datasets/releases](https://github.com/singletongue/wikipedia-utils/releases)
<br>上記リンクよりparagraphs-jawiki-20230403.json.gzを取得

### make_passages_from_paragraphs.py

段落テキストをパッセージ (セクション/段落/文) と呼ばれるテキストのまとまりに分割(文書集合の作成)

```
# 24,387,500 lines from all pages 
$ python make_corpus_from_paragraphs.py \
--paragraphs_file ~/work/wikipedia-utils/20230403/paragraphs-jawiki-20230403.json.gz \
--output_file ~/work/wikipedia-utils/20230403/corpus-jawiki-20230403.txt.gz \
--mecab_option '-d /usr/local/lib/mecab/dic/ipadic-neologd-v0.0.7' \
--min_sentence_length 10 \
--max_sentence_length 1000

# 20,133,720 lines from filtered pages
$ python make_corpus_from_paragraphs.py \
--paragraphs_file ~/work/wikipedia-utils/20230403/paragraphs-jawiki-20230403.json.gz \
--output_file ~/work/wikipedia-utils/20230403/corpus-jawiki-20230403-filtered-large.txt.gz \
--mecab_option '-d /usr/local/lib/mecab/dic/ipadic-neologd-v0.0.7' \
--min_sentence_length 10 \
--max_sentence_length 1000 \
--page_ids_file ~/work/wikipedia-utils/20230403/page-ids-jawiki-20230403.json \
--min_inlinks 10 \
--exclude_sexual_pages 
```

### make_passages_from_paragraphs_passage_db.py

段落テキストをパッセージ (セクション/段落/文) と呼ばれるテキストのまとまりに分割(文集合の作成)

### Elasticsearchの起動
[a](https://www.elastic.co/jp/elasticsearch/?ultron=B-Stack-Trials-APJ-JP-Exact-New&gambit=Stack-Core&blade=adwords-s&hulk=paid&Device=c&thor=elasticsearch&gclid=CjwKCAjwuqiiBhBtEiwATgvixKPNxrel3EHy5-qX92I93BNJzXmpUcfbGoGcRsYnSSHa7kbuy9eQThoC4GsQAvD_BwE)
<br>上記リンクからダウンロード

### build_es_index_passages.py

Elasticsearch インデックスを構築!

```
$ python build_es_index_passages.py \
--passages_file ~/work/wikipedia-utils/20230403/passages-para-jawiki-20230403.json.gz \
--page_ids_file ~/work/wikipedia-utils/20230403/page-ids-jawiki-20230403.json \
--index_name jawiki-20230403-para

$ python build_es_index_passages.py \
--passages_file ~/work/wikipedia-utils/20230403/passages-c400-jawiki-20230403.json.gz \
--page_ids_file ~/work/wikipedia-utils/20230403/page-ids-jawiki-20230403.json \
--index_name jawiki-20230403-c400

$ python build_es_index_passages.py \
--passages_file ~/work/wikipedia-utils/20230403/passages-c300-jawiki-20230403.json.gz \
--page_ids_file ~/work/wikipedia-utils/20230403/page-ids-jawiki-20230403.json \
--index_name jawiki-20230403-c300
```

### データセットの作成


## 位置情報の埋め込み

# -*- coding: utf-8 -*-
import feedparser
import requests
import datetime
import re
import json
import os
import hashlib
import time
import csv
import signal
from collections import Counter
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import random

# ==================== RSS源发现模块（优化版） ====================

class RSSSourceFinder:
    def __init__(self, timeout=15):
        self.timeout = timeout
        # 更丰富的 User-Agent 列表，随机使用以降低被屏蔽风险
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "RSS-Finder-Plus/1.2 (+https://github.com/F-swanlight/)"
        ]
        self.session = self._create_session()
        
    def _create_session(self):
        """创建具有随机User-Agent的会话"""
        session = requests.Session()
        session.headers.update({
            "User-Agent": random.choice(self.user_agents),
            "Accept": "application/rss+xml, application/atom+xml, text/xml, application/xml, */*",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
            "Accept-Encoding": "gzip, deflate, br"
        })
        return session
        
    def _rotate_user_agent(self):
        """轮换User-Agent"""
        self.session.headers.update({"User-Agent": random.choice(self.user_agents)})
    
    def fetch_json(self, url):
        try:
            self._rotate_user_agent()
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[DEBUG] fetch_json error for {url}: {str(e)}")
            return None

    def fetch_resp(self, url, allow_redirects=True, method="GET", headers=None):
        try:
            self._rotate_user_agent()
            h = dict(self.session.headers)
            if headers:
                h.update(headers)
                
            if method == "HEAD":
                r = self.session.head(url, timeout=self.timeout, allow_redirects=allow_redirects, headers=h)
            else:
                # 增加错误处理和重试机制
                max_retries = 2
                r = None
                for attempt in range(max_retries):
                    try:
                        r = self.session.get(url, timeout=self.timeout, allow_redirects=allow_redirects, headers=h)
                        r.raise_for_status()
                        return r
                    except requests.exceptions.RequestException as e:
                        if attempt < max_retries - 1:
                            sleep_time = 1 * (attempt + 1)
                            print(f"[DEBUG] Retry {attempt+1} for {url} after {sleep_time}s: {str(e)}")
                            time.sleep(sleep_time)
                        else:
                            raise e
            return r
        except Exception as e:
            print(f"[DEBUG] fetch_resp error for {url}: {str(e)}")
            return None

    def is_feed_response(self, resp):
        if resp is None:
            return False
            
        ctype = (resp.headers.get("Content-Type") or "").lower()
        
        # 检查头部内容类型
        if any(t in ctype for t in ["application/rss+xml", "application/atom+xml", "application/xml", "text/xml"]):
            try:
                text_head = resp.text[:8192] if hasattr(resp, "text") else ""
                if "<rss" in text_head.lower() or "<feed" in text_head.lower() or "<channel" in text_head.lower():
                    return True
                    
                # 检查是否包含典型的RSS/Atom元素
                if re.search(r'<(item|entry)>', text_head.lower()):
                    return True
            except:
                pass
                
        return False

    def normalize_url(self, u):
        if not u:
            return None
        if u.startswith("//"):
            u = "https:" + u
        if not urlparse(u).scheme:
            u = "https://" + u
        return u

    def get_homepages_from_openalex(self, issn):
        url = f"https://api.openalex.org/sources/ISSN:{issn}"
        data = self.fetch_json(url)
        homes = []
        if data and isinstance(data, dict):
            homepage = self.normalize_url(data.get("homepage_url"))
            if homepage:
                homes.append(homepage)
            for a in data.get("alternate_urls") or []:
                a = self.normalize_url(a)
                if a:
                    homes.append(a)
                    
        # 如果OpenAlex无返回，尝试其他方式
        if not homes and issn:
            try:
                issn_no_dash = issn.replace("-", "")
                homes.append(f"https://www.doi.org/{issn}")
                homes.append(f"https://www.doi.org/{issn_no_dash}")
                homes.extend([
                    f"https://www.sciencedirect.com/journal/{issn}",
                    f"https://onlinelibrary.wiley.com/journal/{issn}",
                    f"https://www.tandfonline.com/journals/{issn}",
                    f"https://journals.sagepub.com/{issn}"
                ])
            except:
                pass
                
        return list(dict.fromkeys(homes))

    def extract_feed_links_from_html(self, url, html):
        try:
            soup = BeautifulSoup(html, "html.parser")
            feed_urls = set()
            
            for link in soup.find_all("link", attrs={"rel": True, "href": True}):
                rels = " ".join([r.lower() for r in link.get("rel") or []])
                typ = (link.get("type") or "").lower()
                if "alternate" in rels and any(t in typ for t in ["rss+xml", "atom+xml", "xml", "rss"]):
                    href = link.get("href")
                    if href:
                        feed_urls.add(urljoin(url, href))
            
            for a in soup.find_all("a", href=True):
                href = a.get("href") or ""
                text = (a.get_text() or "").lower()
                if any(k in href.lower() for k in ["rss", "feed", "atom", "xml", "syndication"]) or \
                   any(k in text.lower() for k in ["rss", "feed", "atom", "xml", "syndication"]):
                    feed_urls.add(urljoin(url, href))
                    
            for meta in soup.find_all("meta"):
                if meta.get("name", "").lower() == "description" and "rss" in meta.get("content", "").lower():
                    urls = re.findall(r'https?://\S+', meta.get("content", ""))
                    for u in urls:
                        if any(k in u.lower() for k in ["rss", "feed", "atom", "xml"]):
                            feed_urls.add(u)
                            
            return list(feed_urls)
        except Exception as e:
            print(f"[DEBUG] extract_feed_links_from_html error: {str(e)}")
            return []

    def discover_official_feeds(self, home_url):
        out = []
        home_url = self.normalize_url(home_url)
        if not home_url:
            return out
            
        print(f"[DEBUG] Checking homepage: {home_url}")

        try:
            resp = self.fetch_resp(home_url)
            if resp is not None:
                for fu in self.extract_feed_links_from_html(home_url, resp.text):
                    print(f"[DEBUG] Testing potential feed: {fu}")
                    r = self.fetch_resp(fu)
                    if self.is_feed_response(r):
                        out.append(fu)
                        print(f"[DEBUG] Found valid feed: {fu}")
        except Exception as e:
            print(f"[DEBUG] Error checking homepage: {str(e)}")

        parsed = urlparse(home_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        bases = [home_url.rstrip("/"), base.rstrip("/")]
        
        suffixes = [
            "rss", "feed", "rss.xml", "atom.xml", "feeds", "index.xml",
            "feed/rss", "rss/feed", "rss/all", "feeds/posts/default",
            "feed.xml", "atom", "syndication", "rss/index", "news/feed",
            "current.rss", "current.xml", "current-issue", "current-issue/feed",
            "current-issue/rss", "latest/rss", "latest.xml"
        ]
        
        for b in bases:
            for suf in suffixes:
                fu = f"{b}/{suf}"
                print(f"[DEBUG] Testing common pattern: {fu}")
                r = self.fetch_resp(fu)
                if self.is_feed_response(r):
                    out.append(fu)
                    print(f"[DEBUG] Found valid feed: {fu}")

        return list(dict.fromkeys(out))

    def try_publisher_specific_feeds(self, journal_title, issn):
        """尝试各大出版社特定的RSS源格式"""
        feeds = []
        
        clean_title = re.sub(r'[^\w\s]', '', journal_title.lower())
        slug = "-".join(clean_title.split())
        
        # 1. Elsevier ScienceDirect
        if issn:
            for val in [issn, issn.replace("-", "")]:
                urls = [
                    f"https://rss.sciencedirect.com/publication/science/{val}",
                    f"https://www.sciencedirect.com/journal/{val}/latest-articles/rss"
                ]
                for url in urls:
                    r = self.fetch_resp(url)
                    if self.is_feed_response(r):
                        feeds.append(url)
        
        # 2. Wiley
        if issn:
            urls = [
                f"https://onlinelibrary.wiley.com/feed/{issn}/most-recent",
                f"https://onlinelibrary.wiley.com/action/showFeed?type=etoc&feed=rss&jc={issn}"
            ]
            for url in urls:
                r = self.fetch_resp(url)
                if self.is_feed_response(r):
                    feeds.append(url)
        
        # 3. Nature
        if slug:
            nature_url = f"https://www.nature.com/{slug}.rss"
            r = self.fetch_resp(nature_url)
            if self.is_feed_response(r):
                feeds.append(nature_url)
            
        # 4. MDPI
        if slug:
            mdpi_url = f"https://www.mdpi.com/rss/journal/{slug}"
            r = self.fetch_resp(mdpi_url)
            if self.is_feed_response(r):
                feeds.append(mdpi_url)
            
        # 5. SpringerLink
        if issn:
            springer_url = f"https://link.springer.com/journal/{issn}.rss"
            r = self.fetch_resp(springer_url)
            if self.is_feed_response(r):
                feeds.append(springer_url)
                
        # 6. Taylor & Francis
        if slug:
            tf_url = f"https://www.tandfonline.com/feed/rss/{slug}"
            r = self.fetch_resp(tf_url)
            if self.is_feed_response(r):
                feeds.append(tf_url)
                
        # 7. SAGE Journals
        if slug:
            sage_url = f"https://journals.sagepub.com/action/showFeed?ui=0&mi=ehikzz&ai=2b4&jc={slug}&type=etoc&feed=rss"
            r = self.fetch_resp(sage_url)
            if self.is_feed_response(r):
                feeds.append(sage_url)
                
        return feeds

    def find_rss_for_journal(self, title, issn):
        """为单个期刊查找RSS源（优化版）"""
        try:
            print(f"\n[DEBUG] Finding RSS for: {title} (ISSN: {issn})")
            
            journal_timeout = 60  # 秒
            start_time = time.time()
            
            publisher_feeds = self.try_publisher_specific_feeds(title, issn)
            if publisher_feeds:
                print(f"[DEBUG] Found publisher-specific feed: {publisher_feeds[0]}")
                return publisher_feeds[0], "publisher_specific"
            
            if time.time() - start_time > journal_timeout:
                print(f"[WARN] 期刊处理超时: {title}")
                return None, "timeout"
            
            homes = self.get_homepages_from_openalex(issn)
            
            for home in homes:
                if time.time() - start_time > journal_timeout:
                    print(f"[WARN] 期刊处理超时: {title}")
                    return None, "timeout"
                feeds = self.discover_official_feeds(home)
                if feeds:
                    print(f"[DEBUG] Found feed from homepage: {feeds[0]}")
                    return feeds[0], "official"
            
            try:
                if time.time() - start_time > journal_timeout:
                    print(f"[WARN] 期刊处理超时: {title}")
                    return None, "timeout"
                search_term = f"{title} journal rss feed"
                search_url = f"https://www.bing.com/search?q={search_term}"
                resp = self.fetch_resp(search_url)
                
                if resp and resp.text:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    links = soup.find_all("a", href=True)
                    for link in links:
                        href = link.get("href", "")
                        if any(term in href.lower() for term in ["rss", "feed", "atom", ".xml"]):
                            r = self.fetch_resp(href)
                            if self.is_feed_response(r):
                                print(f"[DEBUG] Found feed from search: {href}")
                                return href, "search"
            except Exception as e:
                print(f"[DEBUG] Search engine method failed: {str(e)}")
            
            return None, None
        except Exception as e:
            print(f"[ERROR] Journal processing error: {str(e)}")
            return None, "error"

    def update_journal_rss_sources(self, journal_csv_file, output_file="journals_with_rss.csv"):
        """批量更新期刊RSS源"""
        print(f"[INFO] 🔍 开始查找期刊RSS源...")
        
        def timeout_handler(signum, frame):
            raise TimeoutError("RSS处理超时")
        
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(1800)  # 30分钟
        
        rows = []
        results = []
        
        try:
            if not os.path.exists(journal_csv_file):
                print(f"[ERROR] 期刊文件不存在: {journal_csv_file}")
                return []
            if not os.access(journal_csv_file, os.R_OK):
                print(f"[ERROR] 期刊文件无法读取(权限问题): {journal_csv_file}")
                return []
            try:
                with open(journal_csv_file, "r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
            except UnicodeDecodeError:
                print(f"[ERROR] 文件编码问题，尝试不同编码...")
                with open(journal_csv_file, "r", encoding="latin-1", newline="") as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
        except Exception as e:
            print(f"[ERROR] 读取期刊文件失败: {str(e)}")
            return []
        
        total = len(rows)
        found_count = 0
        
        try:
            for i, row in enumerate(rows, 1):
                title = row.get("title", "").strip()
                issn = row.get("issn", "").strip()
                zone = row.get("zone", "").strip()
                
                print(f"[INFO] 📖 处理进度: {i}/{total} - {title[:50]}...")
                
                rss_url, rss_source = self.find_rss_for_journal(title, issn)
                
                result = {
                    "index": row.get("index", i),
                    "title": title,
                    "issn": issn,
                    "zone": zone,
                    "rss_url": rss_url or "",
                    "rss_source": rss_source or ""
                }
                results.append(result)
                
                if rss_url:
                    found_count += 1
                    print(f"[SUCCESS] ✅ 找到RSS源: {rss_source}")
                else:
                    print(f"[WARN] ❌ 未找到RSS源")
                
                # 速率控制
                if i % 10 == 0:  # 每10个较长等待
                    wait_time = random.uniform(5, 10)
                    print(f"[INFO] 较长等待 {wait_time:.1f} 秒...")
                    time.sleep(wait_time)
                elif i % 3 == 0:
                    wait_time = random.uniform(2, 5)
                    print(f"[INFO] 等待 {wait_time:.1f} 秒...")
                    time.sleep(wait_time)
                
                # 定期保存临时结果
                if i % 20 == 0:
                    try:
                        temp_file = output_file + ".temp"
                        fieldnames = ["index", "title", "issn", "zone", "rss_url", "rss_source"]
                        with open(temp_file, "w", encoding="utf-8", newline="") as f:
                            writer = csv.DictWriter(f, fieldnames=fieldnames)
                            writer.writeheader()
                            writer.writerows(results)
                        print(f"[INFO] 💾 临时结果已保存至: {temp_file}")
                    except Exception as e:
                        print(f"[WARN] 保存临时结果失败: {str(e)}")
        except TimeoutError:
            print("[ERROR] RSS源查找处理超时，返回已处理的结果")
        finally:
            signal.alarm(0)
        
        try:
            fieldnames = ["index", "title", "issn", "zone", "rss_url", "rss_source"]
            with open(output_file, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(results)
            print(f"[INFO] 📊 RSS源查找完成: {found_count}/{total} 个期刊找到RSS源")
            print(f"[INFO] 💾 结果已保存至: {output_file}")
        except Exception as e:
            print(f"[ERROR] 保存RSS结果失败: {str(e)}")
        
        return [r for r in results if r["rss_url"]]

# ==================== 主推送系统 ====================

CORE_KEYWORDS = [
    "碳酸盐岩", "carbonate", "carbonate rock", "limestone", "灰岩", "白云岩", "dolomite", "dolomitic",
    "微生物矿化", "microbialite", "microbial mineralization", "biomineralization", "microbial carbonate",
    "天然氢", "natural hydrogen", "白氢", "white hydrogen", "native hydrogen", "geological hydrogen",
    "大洋氧化", "ocean oxidation", "ocean redox", "oceanic oxidation", "marine oxidation", "redox evolution"
]

AUXILIARY_KEYWORDS = [
    "反应网络", "reaction network", "reacnetgenerator", "分子动力学", "molecular dynamics", "MD simulation",
    "机器学习", "machine learning", "AI", "artificial intelligence", "生成式AI", "generative AI",
    "数据挖掘", "data mining", "深度学习", "deep learning", "神经网络", "neural network",
    "地球化学", "geochemistry", "矿化", "mineralization", "沉积", "sedimentary",
    "古环境", "paleoenvironment", "成岩", "diagenesis", "黄铁矿", "pyrite",
    "氧化", "oxidation", "氧", "oxygen", "海洋", "marine", "deep sea",
    "simulation", "modeling", "computational", "numerical", "fold", "folding",
    "构造", "structure", "tectonics", "地层", "stratigraphy", "deformation"
]

ZONE_WEIGHTS = {
    "1区": 50,
    "2区": 30,
    "3区": 20,
    "4区": 10,
    "": 15


WECHAT_WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=d'saho'ifvhaDVBAVNSOVSNAP"

EXCLUDED_KEYWORDS = [
    "carbonate", "limestone", "dolomite", "microbial", "hydrogen", "oxidation", "ocean",
    "碳酸盐", "灰岩", "白云岩", "微生物", "氢", "氧化", "海洋", "矿化"
]

HISTORY_FILE = "pushed_articles.json"
PUSH_SCHEDULE_FILE = "push_schedule.json"
RSS_STATUS_FILE = "rss_status.json"
JOURNAL_RSS_FILE = "journals_with_rss.csv"
JOURNAL_LIST_FILE = "journals_1-260.csv"
HISTORY_DAYS = 60
DUPLICATE_CHECK_DAYS = 7
MAX_PUSH_PER_BATCH = 6

TRANSLATE_API_URL = "https://api.mymemory.translated.net/get"

# 每周更新RSS源的星期配置：Python中周一=0，周日=6
WEEKLY_RSS_UPDATE_DAY = 6  # 周日

def load_rss_feeds_from_csv(csv_file):
    feeds = []
    try:
        with open(csv_file, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rss_url = row.get("rss_url", "").strip()
                if rss_url:
                    feeds.append({
                        "url": rss_url,
                        "title": row.get("title", ""),
                        "source": row.get("rss_source", ""),
                        "zone": row.get("zone", ""),
                        "issn": row.get("issn", "")
                    })
    except FileNotFoundError:
        print(f"[WARN] RSS源文件不存在: {csv_file}")
    return feeds

def translate_to_chinese(text):
    try:
        if any('\u4e00' <= char <= '\u9fff' for char in text):
            return text
        if len(text) > 200:
            text = text[:200] + "..."
        params = {'q': text, 'langpair': 'en|zh-CN'}
        response = requests.get(TRANSLATE_API_URL, params=params, timeout=5)
        response.raise_for_status()
        result = response.json()
        if result.get('responseStatus') == 200:
            translated = result.get('responseData', {}).get('translatedText', '')
            if translated and translated != text:
                return translated
        return text
    except Exception as e:
        print(f"[WARN] 翻译失败: {e}")
        return text

def load_rss_status():
    if os.path.exists(RSS_STATUS_FILE):
        try:
            with open(RSS_STATUS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[ERROR] 读取RSS状态失败: {e}")
            return {}
    return {}

def save_rss_status(status):
    try:
        with open(RSS_STATUS_FILE, 'w', encoding='utf-8') as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] 保存RSS状态失败: {e}")

def load_pushed_articles():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[ERROR] 读取历史记录失败: {e}")
            return {}
    return {}

def save_pushed_articles(pushed_articles):
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(pushed_articles, f, ensure_ascii=False, indent=2)
        print(f"[INFO] 历史记录已保存")
    except Exception as e:
        print(f"[ERROR] 保存历史记录失败: {e}")

def load_push_schedule():
    if os.path.exists(PUSH_SCHEDULE_FILE):
        try:
            with open(PUSH_SCHEDULE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[ERROR] 读取推送计划失败: {e}")
            return {}
    return {}

def save_push_schedule(schedule):
    try:
        with open(PUSH_SCHEDULE_FILE, 'w', encoding='utf-8') as f:
            json.dump(schedule, f, ensure_ascii=False, indent=2)
        print(f"[INFO] 推送计划已保存")
    except Exception as e:
        print(f"[ERROR] 保存推送计划失败: {e}")

def clean_old_records(pushed_articles, days=HISTORY_DAYS):
    cutoff_date = datetime.datetime.now() - datetime.timedelta(days=days)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d")
    keys_to_remove = [k for k in pushed_articles if k < cutoff_str]
    for key in keys_to_remove:
        del pushed_articles[key]
    if keys_to_remove:
        print(f"[INFO] 清理了 {len(keys_to_remove)} 天的旧记录")

def generate_article_hash(title, link):
    title = (title or "").strip()
    link = (link or "").strip()
    content = f"{title}||{link}"
    return hashlib.md5(content.encode('utf-8')).hexdigest()

def is_article_duplicate(article_hash, pushed_articles, today):
    current_date = datetime.datetime.strptime(today, "%Y-%m-%d")
    for i in range(DUPLICATE_CHECK_DAYS):
        check_date = (current_date - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        if check_date in pushed_articles and article_hash in pushed_articles[check_date]:
            return True
    return False

def extract_publication_date(entry):
    for date_field in ['published', 'updated', 'pubDate', 'date']:
        if date_field in entry and entry[date_field]:
            try:
                date_str = entry[date_field]
                formats = [
                    '%a, %d %b %Y %H:%M:%S %z',
                    '%a, %d %b %Y %H:%M:%S %Z',
                    '%Y-%m-%dT%H:%M:%S%z',
                    '%Y-%m-%dT%H:%M:%SZ',
                    '%Y-%m-%d %H:%M:%S',
                    '%Y-%m-%d',
                ]
                for fmt in formats:
                    try:
                        dt = datetime.datetime.strptime(date_str, fmt)
                        return dt.strftime('%Y-%m-%d')
                    except:
                        continue
                date_match = re.search(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})', date_str)
                if date_match:
                    return date_match.group(1).replace('/', '-')
            except:
                pass
    return None

def calculate_priority_score(text, zone=""):
    text_lower = text.lower()
    core_matches = sum(1 for k in CORE_KEYWORDS if k.lower() in text_lower)
    aux_matches = sum(1 for k in AUXILIARY_KEYWORDS if k.lower() in text_lower)
    keyword_score = core_matches * 10 + aux_matches * 1
    zone_weight = ZONE_WEIGHTS.get(zone, ZONE_WEIGHTS[""])
    priority_score = keyword_score + zone_weight
    return priority_score, core_matches, aux_matches, zone_weight

def extract_meaningful_phrases(text):
    text = re.sub(r'[^\w\s\u4e00-\u9fff-]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    phrases = []
    english_phrases = re.findall(r'\b[A-Za-z][\w-]*(?:\s+[A-Za-z][\w-]*){1,3}\b', text)
    for phrase in english_phrases:
        phrase = phrase.strip().lower()
        words = phrase.split()
        if 2 <= len(words) <= 4 and 6 <= len(phrase) <= 40:
            if not all(w.isdigit() for w in words) and not all(len(w) <= 2 for w in words):
                should_exclude = any(ex.lower() in phrase for ex in EXCLUDED_KEYWORDS)
                stop_phrases = ["in the", "of the", "and the", "for the", "this is", "there are"]
                if any(phrase.startswith(stop) for stop in stop_phrases):
                    should_exclude = True
                if not should_exclude:
                    phrases.append(phrase)
    chinese_phrases = re.findall(r'[\u4e00-\u9fff]{2,8}', text)
    for phrase in chinese_phrases:
        if 2 <= len(phrase) <= 8:
            if not any(ex in phrase for ex in EXCLUDED_KEYWORDS):
                phrases.append(phrase)
    return phrases

def has_core_keywords(text):
    text_lower = text.lower()
    return any(keyword.lower() in text_lower for keyword in CORE_KEYWORDS)

def filter_articles(feed_info, today, pushed_articles, rss_status):
    feed_url = feed_info["url"]
    feed_title = feed_info.get("title", "")
    feed_zone = feed_info.get("zone", "")
    
    headers = {
        'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
    }
    
    try:
        zone_display = f"[{feed_zone}]" if feed_zone else ""
        print(f"[INFO] 🔍 正在读取RSS: {feed_title[:30]}...{zone_display} ({feed_info.get('source', 'unknown')})")
        
        max_retries = 3
        resp = None
        for attempt in range(max_retries):
            try:
                resp = requests.get(feed_url, timeout=15, headers=headers, allow_redirects=True)
                resp.raise_for_status()
                break
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    print(f"[WARN] 第{attempt+1}次尝试失败，等待重试: {e}")
                    time.sleep(2)
                    continue
                else:
                    raise e
        
        rss_status[feed_url] = {
            'last_success': today,
            'status': 'success',
            'error': None,
            'journal': feed_title,
            'zone': feed_zone
        }
        
        feed = feedparser.parse(resp.content)
        if not hasattr(feed, 'entries') or len(feed.entries) == 0:
            print(f"[WARN] RSS源返回空内容: {feed_title}")
            rss_status[feed_url]['status'] = 'empty'
            return [], []
        
        filtered_articles = []
        all_meaningful_phrases = []
        duplicate_count = 0
        total_articles = len(feed.entries)
        
        for entry in feed.entries:
            title = entry.title or ""
            link = entry.link or ""
            summary = entry.get("summary", "") or entry.get("description", "") or ""
            pub_date = extract_publication_date(entry)
            article_hash = generate_article_hash(title, link)
            if is_article_duplicate(article_hash, pushed_articles, today):
                duplicate_count += 1
                continue
            text = title + " " + summary
            meaningful_phrases = extract_meaningful_phrases(text)
            all_meaningful_phrases.extend(meaningful_phrases)
            if has_core_keywords(text):
                priority_score, core_matches, aux_matches, zone_weight = calculate_priority_score(text, feed_zone)
                chinese_title = translate_to_chinese(title)
                article_info = {
                    'title': title,
                    'chinese_title': chinese_title,
                    'link': link,
                    'hash': article_hash,
                    'priority_score': priority_score,
                    'core_matches': core_matches,
                    'aux_matches': aux_matches,
                    'zone': feed_zone,
                    'zone_weight': zone_weight,
                    'source': feed_title,
                    'source_type': feed_info.get('source', 'unknown'),
                    'text': text,
                    'pub_date': pub_date or "未知日期"
                }
                filtered_articles.append(article_info)
        
        print(f"[INFO] ✅ 共{total_articles}篇，筛选{len(filtered_articles)}条核心匹配，跳过{duplicate_count}条重复")
        return filtered_articles, all_meaningful_phrases
        
    except requests.exceptions.Timeout:
        error_msg = "⏰ 请求超时"
        print(f"[ERROR] {feed_title} {error_msg}")
        rss_status[feed_url] = {'last_attempt': today, 'status': 'timeout', 'error': error_msg, 'journal': feed_title, 'zone': feed_zone}
        return [], []
    except requests.exceptions.HTTPError as e:
        error_msg = f"HTTP错误: {e.response.status_code}"
        print(f"[ERROR] {feed_title} {error_msg}")
        rss_status[feed_url] = {'last_attempt': today, 'status': 'http_error', 'error': error_msg, 'journal': feed_title, 'zone': feed_zone}
        return [], []
    except requests.exceptions.ConnectionError:
        error_msg = "连接错误"
        print(f"[ERROR] {feed_title} {error_msg}")
        rss_status[feed_url] = {'last_attempt': today, 'status': 'connection_error', 'error': error_msg, 'journal': feed_title, 'zone': feed_zone}
        return [], []
    except Exception as e:
        error_msg = f"未知错误: {str(e)}"
        print(f"[ERROR] {feed_title} {error_msg}")
        rss_status[feed_url] = {'last_attempt': today, 'status': 'unknown_error', 'error': error_msg, 'journal': feed_title, 'zone': feed_zone}
        return [], []

def format_article_for_push(article, index):
    source_name = article.get('source', 'Unknown')
    zone = article.get('zone', '')
    zone_display = f" [{zone}]" if zone else ""
    pub_date = article.get('pub_date', '未知日期')
    display_title = article['chinese_title'] if article['chinese_title'] != article['title'] else article['title']
    result = f"📄 {index}. {display_title}"
    if article['chinese_title'] != article['title']:
        result += f"\n🔤 原标题: {article['title']}"
    result += f"\n🏛️ 来源: {source_name}{zone_display}\n📅 日期: {pub_date}\n🔗 链接: {article['link']}"
    return result

def find_historical_articles(pushed_articles, push_schedule, today, needed_count):
    if needed_count <= 0:
        return []
    print(f"[INFO] 🔍 查找历史未推送文章，需要补充 {needed_count} 篇")
    pushed_hashes = set()
    for date_key in pushed_articles:
        for article_hash in pushed_articles[date_key]:
            pushed_hashes.add(article_hash)
    scheduled_hashes = set()
    if today in push_schedule:
        for article in push_schedule[today]:
            scheduled_hashes.add(article['hash'])
    all_historical_articles = []
    history_schedule_files = []
    for i in range(1, 11):
        past_date = (datetime.datetime.now() - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        filename = f"push_schedule_{past_date}.json"
        if os.path.exists(filename):
            history_schedule_files.append((past_date, filename))
    for date_str, filename in history_schedule_files:
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                history_data = json.load(f)
                if date_str in history_data:
                    for article in history_data[date_str]:
                        if article['hash'] not in pushed_hashes and article['hash'] not in scheduled_hashes:
                            all_historical_articles.append(article)
        except Exception as e:
            print(f"[WARN] 读取历史推送计划失败 {filename}: {e}")
    if len(all_historical_articles) < needed_count:
        for date_key in sorted(push_schedule.keys()):
            if date_key < today:
                for article in push_schedule[date_key]:
                    if article['hash'] not in pushed_hashes and article['hash'] not in scheduled_hashes:
                        all_historical_articles.append(article)
    all_historical_articles = sorted(all_historical_articles, key=lambda x: x['priority_score'], reverse=True)
    return all_historical_articles[:needed_count]

def get_top_meaningful_phrases(all_phrases, top_n=5):
    if not all_phrases:
        return []
    normalized_phrases = [p.strip() for p in all_phrases if p and len(p.strip()) >= 4]
    if not normalized_phrases:
        return []
    phrase_count = Counter(normalized_phrases)
    return phrase_count.most_common(top_n)

def push_to_wechat(text):
    print(f"[INFO] 📤 准备推送内容，长度为 {len(text)} 字符")
    maxlen = 1800
    for i in range(0, len(text), maxlen):
        chunk = text[i:i+maxlen]
        try:
            response = requests.post(WECHAT_WEBHOOK, json={"msgtype": "text", "text": {"content": chunk}})
            print(f"[INFO] 微信推送响应: {response.text}")
            if response.status_code != 200:
                print(f"[ERROR] 推送失败，状态码: {response.status_code}")
        except Exception as e:
            print(f"[ERROR] 推送到微信时出错: {e}")

def get_rss_status_summary(rss_status, total_feeds):
    success = len([s for s in rss_status.values() if s.get('status') == 'success'])
    failed = total_feeds - success
    zone_stats = {}
    for status in rss_status.values():
        if status.get('status') == 'success':
            zone = status.get('zone', '未知')
            zone_stats[zone] = zone_stats.get(zone, 0) + 1
    return {
        'total': total_feeds,
        'success': success,
        'failed': failed,
        'success_rate': round((success / total_feeds * 100) if total_feeds > 0 else 0, 1),
        'zone_stats': zone_stats
    }

def main():
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    current_time = datetime.datetime.now().strftime("%H:%M:%S")
    
    print(f"\n[INFO] 🏔️ 折叠地层推送系统启动 [分区评分优化版 v2.0]")
    print(f"[INFO] 📅 日期: {today} ⏰ 时间: {current_time}")
    print(f"[INFO] 👤 用户: F-swanlight")
    print(f"[INFO] 🎯 每批次最多推送: {MAX_PUSH_PER_BATCH} 篇")
    print(f"[INFO] 🎯 分区权重: 1区(+50) 2区(+30) 3区(+20) 4区(+10)")
    
    print(f"\n[INFO] 🔄 第一步：更新期刊RSS源（仅在每周日执行）...")
    rss_finder = RSSSourceFinder(timeout=12)

    # 是否强制更新（环境变量FORCE_RSS_UPDATE=1 可临时覆盖周日限制）
    force_update_flag = os.getenv("FORCE_RSS_UPDATE", "").strip() == "1"
    is_sunday = datetime.datetime.now().weekday() == WEEKLY_RSS_UPDATE_DAY
    
    should_update_rss = False
    if force_update_flag:
        print("[INFO] ⚠️ 环境变量 FORCE_RSS_UPDATE=1 已设置，本次将强制更新RSS源（忽略周日限制）")
        should_update_rss = True
    else:
        if is_sunday:
            # 周日执行：若今日尚未更新则执行
            if os.path.exists(JOURNAL_RSS_FILE):
                mtime = os.path.getmtime(JOURNAL_RSS_FILE)
                mdate = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
                if mdate == today:
                    should_update_rss = False
                    print(f"[INFO] ✅ 今日（周日）RSS源文件已更新，跳过重复更新")
                else:
                    should_update_rss = True
                    print(f"[INFO] 📅 周日且今日未更新，将执行RSS源发现")
            else:
                should_update_rss = True
                print(f"[INFO] 📅 周日且不存在RSS源文件，将执行首次生成")
        else:
            # 非周日默认跳过；若文件不存在则首次生成
            if os.path.exists(JOURNAL_RSS_FILE):
                should_update_rss = False
                print(f"[INFO] 📆 非周日，跳过RSS源发现（使用现有文件）")
            else:
                should_update_rss = True
                print(f"[WARN] 📁 未发现RSS源文件，尽管非周日，仍将执行首次生成以保证可用")

    SKIP_RSS_UPDATE = False  # 测试时设为 True
    if SKIP_RSS_UPDATE:
        should_update_rss = False
        print("[INFO] ⚠️ 测试模式：跳过RSS源更新")
    
    if should_update_rss:
        if os.path.exists(JOURNAL_LIST_FILE):
            _ = rss_finder.update_journal_rss_sources(JOURNAL_LIST_FILE, JOURNAL_RSS_FILE)
        else:
            print(f"[WARN] 期刊列表文件不存在: {JOURNAL_LIST_FILE}")
    
    print(f"\n[INFO] 🔄 第二步：加载RSS源...")
    rss_feeds = load_rss_feeds_from_csv(JOURNAL_RSS_FILE)
    additional_feeds = [
        {"url": "https://eos.org/feed", "title": "Eos", "source": "additional", "zone": ""},
        {"url": "https://www.sciencedaily.com/rss/earth_climate/geology.xml", "title": "Science Daily Geology", "source": "additional", "zone": ""},
        {"url": "https://news.agu.org/feed/", "title": "AGU News", "source": "additional", "zone": ""},
        {"url": "https://phys.org/rss-feed/earth-news/", "title": "Phys.org Earth News", "source": "additional", "zone": ""},
        {"url": "https://export.arxiv.org/rss/physics.geo-ph", "title": "arXiv Geophysics", "source": "additional", "zone": ""},
        {"url": "http://news.sciencenet.cn/rss/Earth.xml", "title": "科学网地球科学", "source": "additional", "zone": ""}
    ]
    rss_feeds.extend(additional_feeds)
    
    zone_counts = {}
    for feed in rss_feeds:
        zone = feed.get('zone', '其他')
        zone_counts[zone] = zone_counts.get(zone, 0) + 1
    
    print(f"[INFO] 📊 总共加载RSS源: {len(rss_feeds)} 个")
    print(f"[INFO] 📊 分区分布: {', '.join([f'{z}({c}个)' for z, c in sorted(zone_counts.items())])}")
    
    print(f"\n[INFO] 🔄 第三步：处理文章...")
    pushed_articles = load_pushed_articles()
    push_schedule = load_push_schedule()
    rss_status = load_rss_status()
    clean_old_records(pushed_articles)
    
    if today not in push_schedule:
        push_schedule[today] = []
    
    all_articles = []
    all_meaningful_phrases = []
    
    def process_timeout_handler(signum, frame):
        raise TimeoutError("处理超时")
    signal.signal(signal.SIGALRM, process_timeout_handler)
    signal.alarm(3600)  # 1小时
    
    try:
        print(f"[INFO] 🔄 开始处理 {len(rss_feeds)} 个RSS源...")
        for i, feed_info in enumerate(rss_feeds, 1):
            print(f"[INFO] 📈 处理进度: {i}/{len(rss_feeds)}")
            articles, phrases = filter_articles(feed_info, today, pushed_articles, rss_status)
            all_articles.extend(articles)
            all_meaningful_phrases.extend(phrases)
            if i % 5 == 0:
                wait_time = random.uniform(1, 2.5)
                print(f"[INFO] 等待 {wait_time:.1f} 秒...")
                time.sleep(wait_time)
            if i % 20 == 0:
                save_rss_status(rss_status)
                print(f"[INFO] 已保存当前RSS状态 ({i}/{len(rss_feeds)})")
        signal.alarm(0)
    except TimeoutError:
        print("[WARN] RSS源处理超时，使用已处理结果继续")
    except Exception as e:
        print(f"[ERROR] RSS源处理出错: {str(e)}")
    finally:
        signal.alarm(0)
        save_rss_status(rss_status)
    
    for article in all_articles:
        if article['hash'] not in [a['hash'] for a in push_schedule[today]]:
            push_schedule[today].append(article)
    
    push_schedule[today] = sorted(push_schedule[today], key=lambda x: x['priority_score'], reverse=True)
    
    if push_schedule[today]:
        print(f"\n[INFO] 🎯 优先级最高的文章:")
        for i, article in enumerate(push_schedule[today][:5], 1):
            zone_info = f"[{article['zone']}]" if article['zone'] else "[无分区]"
            print(f"  {i}. {article['title'][:60]}... {zone_info} (分数:{article['priority_score']})")
    
    rss_summary = get_rss_status_summary(rss_status, len(rss_feeds))
    
    print(f"\n[INFO] 🔄 第四步：准备推送...")
    first_batch = push_schedule[today][:MAX_PUSH_PER_BATCH]
    remaining = push_schedule[today][MAX_PUSH_PER_BATCH:]
    
    if len(first_batch) < MAX_PUSH_PER_BATCH:
        needed_count = MAX_PUSH_PER_BATCH - len(first_batch)
        historical_articles = find_historical_articles(pushed_articles, push_schedule, today, needed_count)
        if historical_articles:
            print(f"[INFO] 📚 从历史文章补充了 {len(historical_articles)} 篇")
            first_batch.extend(historical_articles)
    
    second_batch = []
    if len(remaining) > 0:
        second_batch = remaining[:MAX_PUSH_PER_BATCH]
        push_schedule[today] = remaining[MAX_PUSH_PER_BATCH:]
    else:
        push_schedule[today] = []
    
    if first_batch:
        print(f"[INFO] ✅ 第一批次推送 {len(first_batch)} 篇文章")
        push_content = [format_article_for_push(article, i+1) for i, article in enumerate(first_batch)]
        content = (
            f"【🏔️ 折叠地层推送】{today} (1/{'2' if second_batch else '1'})\n\n"
            f"{chr(10).join(push_content)}\n\n"
            "📊 推送统计:\n"
            f"🎯 第一批次: {len(first_batch)}/{MAX_PUSH_PER_BATCH} 篇\n"
            f"🔍 今日发现: {len(all_articles)} 篇新文章\n"
            f"🌐 RSS成功率: {rss_summary['success_rate']}% ({rss_summary['success']}/{rss_summary['total']})"
        )
        top_phrases = get_top_meaningful_phrases(all_meaningful_phrases, 5)
        if top_phrases:
            content += "\n\n🔥 今日热点短语TOP5："
            for i, (phrase, count) in enumerate(top_phrases, 1):
                content += f"\n🏆 {i}. {phrase}: {count}次"
        else:
            content += "\n\n🔥 今日热点短语：\n🚫 暂无明显热点短语"
        content += f"\n\n⏰ 推送时间: {current_time}"
        push_to_wechat(content)
        
        if today not in pushed_articles:
            pushed_articles[today] = []
        for article in first_batch:
            pushed_articles[today].append(article['hash'])
        save_pushed_articles(pushed_articles)
        save_push_schedule(push_schedule)
        
        if second_batch:
            print("[INFO] 🕒 等待5秒后推送第二批...")
            time.sleep(5)
            current_time = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"[INFO] ✅ 第二批次推送 {len(second_batch)} 篇文章")
            push_content = [format_article_for_push(article, i+1) for i, article in enumerate(second_batch)]
            content2 = (
                f"【🏔️ 折叠地层推送】{today} (2/2)\n\n"
                f"{chr(10).join(push_content)}\n\n"
                "📊 推送统计:\n"
                f"🎯 第二批次: {len(second_batch)}/{MAX_PUSH_PER_BATCH} 篇\n"
                f"📋 队列剩余: {len(push_schedule[today])} 篇\n"
                f"🔍 总计发现: {len(all_articles)} 篇新文章\n\n"
                f"⏰ 推送时间: {current_time}"
            )
            push_to_wechat(content2)
            for article in second_batch:
                pushed_articles[today].append(article['hash'])
            save_pushed_articles(pushed_articles)
    else:
        print("[INFO] ❌ 今日无新的核心关键词匹配文章")
        content = (
            f"【🏔️ 折叠地层推送】{today}\n\n"
            "📝 今日无新的核心关键词匹配文章\n"
            f"🔍 已检索 {rss_summary['total']} 个RSS源\n"
            f"✅ 成功获取 {rss_summary['success']} 个源\n"
            f"❌ 失败 {rss_summary['failed']} 个源 (成功率: {rss_summary['success_rate']}%)\n"
            f"💭 全域短语提取: {len(all_meaningful_phrases)} 个"
        )
        top_phrases = get_top_meaningful_phrases(all_meaningful_phrases, 5)
        if top_phrases:
            content += "\n\n🔥 今日热点短语TOP5："
            for i, (phrase, count) in enumerate(top_phrases, 1):
                content += f"\n🏆 {i}. {phrase}: {count}次"
        else:
            content += "\n\n🔥 今日热点短语：\n🚫 暂无明显热点短语"
        content += f"\n\n⏰ 推送时间: {current_time}"
        push_to_wechat(content)
    
    print(f"[INFO] 🎉 推送完成! 今日已推送: {len(pushed_articles.get(today, []))}")
    print(f"[INFO] 📊 RSS源统计: 成功{rss_summary['success']}/失败{rss_summary['failed']}/总计{rss_summary['total']} (成功率{rss_summary['success_rate']}%)")
    
    backup_file = f"push_schedule_{today}.json"
    try:
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(push_schedule, f, ensure_ascii=False, indent=2)
        print(f"[INFO] 📦 已创建推送计划备份: {backup_file}")
    except Exception as e:
        print(f"[ERROR] 备份推送计划失败: {e}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FATAL ERROR] ❌ 主程序执行失败: {e}")
        error_content = (
            "【🚨 折叠地层推送系统错误】\n"
            f"❌ 系统运行出错: {str(e)}\n"
            f"⏰ 错误时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            "👤 用户: F-swanlight\n"
            "🔧 请检查系统状态"
        )
        try:
            push_to_wechat(error_content)
        except:
            pass
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用 DeepSeek（OpenAI 兼容接口）把视频号口播转写稿整理成可发布文案。

规则全部来自用户 2026-09-11 的硬性要求：
  - 单条讲单条：不对比、不做共同分析
  - 不站创作者视角：只提炼内容本身讲了什么
  - 完整文案不截断
  - 精华/重点 **加粗**
  - 转写错字直接改；拿不准标「疑似」；所有"注：xx→yy"不外发
  - 顶部 3~5 条关键要点

依赖：仅标准库（urllib），不依赖 openai 包。代理走系统环境变量。
若 key 缺失或调用失败，退化为「标题+原始稿」结构，保证流水线不中断。
"""
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, ".sph_config.json")

SYSTEM = (
    "你是中文视频号口播稿整理助手，擅长在保持原意的前提下纠正机器转写错误、"
    "提炼关键要点、突出核心重点。输出必须严格遵循用户指定的格式，不添加任何多余文字。"
)


def load_config():
    """配置来源（后者优先）：tools/.sph_config.json → 环境变量。

    环境变量（跨平台通用，CI 里也方便）：
        DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
    """
    try:
        cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
    except Exception:
        cfg = {}
    for env_k in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
        v = os.environ.get(env_k)
        if v:
            cfg[env_k] = v
    return cfg


def human_dur(sec):
    if not sec:
        return ""
    m, s = divmod(int(sec), 60)
    return f"{m}分{s:02d}秒" if m else f"{s}秒"


# 专有名词白名单：口播里这些写法都是正确的，绝不是错别字，一律原样保留、绝不"纠正"。
# 用户 2026-09-11 明确要求：这些不是错别字。后续新增模型名直接往下加即可。
# 专有名词清单（L3 提示兜底）：**唯一维护点是 fix_terms.py 的 TERMS 表**，
# 这里直接引用，避免「词表两处维护、改了这边忘那边」。
sys.path.insert(0, HERE)
import fix_terms  # noqa: E402

GLOSSARY = fix_terms.prompt_lines("terms")
GLOSSARY_NAMES = fix_terms.names_glossary()      # 紧凑版：只给拼写，不给释义（省 token、不稀释注意力）


def _glossary_text():
    lines = ["以下均为正确写法，绝不是错别字，必须一字不改原样保留，禁止改动或标注「疑似」："]
    for name, desc in GLOSSARY:
        lines.append(f"- {name}：{desc}")
    lines.append("")
    lines.append("以下是各行业的公认写法（公司 / 创始人 / 模型 / 术语，括号内为别称或简写）。"
                 "文中若出现发音相近但写错的版本，请直接改成这里的正确写法；这里没有的一律不动：")
    lines.extend(GLOSSARY_NAMES)
    return "\n".join(lines)


PROMPT_TMPL = """请整理下面这条视频号的口播转写稿。这是机器识别的文字，可能有同音字或专有名词错误。

【输出格式】
✅《{title}》
{meta}
【关键要点】
（补 3~5 条，纯内容提炼）
【完整文案】
（口播全文，完整不截断）

【整理规则】
1. 关键要点：只提炼这条视频本身讲了什么知识点/信息。要求：① 不要和别的视频对比、不要做共同分析（用户单独发哪条就只讲哪条）；② 不要站在内容创作者视角（不要写"对选题有什么用""怎么套用""钩子拆解"之类）。读者只是来学内容本身。
2. 完整文案（**可读性优先**，2026-09-20 用户明确要求）——四条同时满足：
   ① **一句不少**：全文保留，不概括、不缩写、不删任何信息点。
   ② **分段/分条**：原文有明确结构（"第一…第二…"、"首先/其次/最后"、明显在讲几点）就**按结构分条**，
      每条前加序号（`1.` `2.` …）+ 该条的标题式短句（若有），条与条之间空行；没有明显结构就**按语义分段**，
      每段 2~4 句，段间空行。**绝不允许输出一整坨连续大段落。**
   ③ **删语气词与口语填充**：狠删 啊/呀/呢/吧/嘛/嗯/哦/唉、无实义的 那个/这个、纯连接词的 然后/就是说、
      对吧/对不对/你知道吧/我跟你讲，以及改口、重复、结巴（"就是就是""我们我们"）。
      删完句子要通顺，必要时补一个标点；但**不要改成书面语**，原说话人的口吻要留住。
   ④ 原文没有标点时按语义补上标点，但不改动原意。长稿也照常完整输出。
3. 加粗精华：在【关键要点】和【完整文案】里，把你认为精华、重要的内容用 ** 加粗（关键词、数字、核心结论），让视觉有重心。
4. 基础纠错（她 2026-09-20 要求：纠正基础错误，但不是改内容那种润色）：
   - **确定的错误必须改回来**：同音字/形近字、人名、书名、公司名、产品名、地名等常识性错误——
     例如把「李尚龙」听成「李生龙」、把「Anthropic」听成别的，都按公认正确写法改。
   - 明显读不通的转写错误、量词/单位明显错的（"美元"听成"美圆"之类）也一并改。
   - 仅此为止：**不改写句子、不调整结构、不换用词、不增删观点**，只让文字回到"没有低级错误"的程度；
     真拿不准的一字不动。所有"注：xx→yy"这类纠错说明一律不要输出，你自己改完即可。
5. 专有名词保护：以下均为正确写法，绝不是错别字，必须一字不改原样保留，禁止改动或标注「疑似」。
{glossary}
6. 只输出上面的格式内容，不要输出任何额外解释、开场白或结束语。

【话题标签】{tags}

【口播转写全文】
{text}"""


def build_prompt(title, author, duration, chars, tags, text):
    meta = " · ".join([x for x in [author, human_dur(duration), f"{chars}字"] if x])
    return PROMPT_TMPL.format(
        title=title, meta=meta, tags=tags, text=text, glossary=_glossary_text()
    )


def _call_deepseek(cfg, messages, timeout):
    key = cfg.get("DEEPSEEK_API_KEY")
    base = (cfg.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = cfg.get("DEEPSEEK_MODEL") or "deepseek-chat"
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d["choices"][0]["message"]["content"].strip()


def _clean(raw):
    # 去掉可能出现的代码围栏
    raw = re.sub(r"^```(?:markdown)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw.strip())
    # 若模型加了多余开场白，从第一个 ✅《 开始截取
    i = raw.find("✅《")
    if i > 0:
        raw = raw[i:]
    return raw.strip()


def _fallback(title, author, duration, chars, tags, text, reason=""):
    meta = " · ".join([x for x in [author, human_dur(duration), f"{chars}字"] if x])
    print(f"[format_doc] 退回原始稿：{reason}", file=sys.stderr)
    return f"✅《{title}》\n{meta}\n\n【关键要点】\n（自动整理不可用，已退化为原始稿）\n\n【完整文案】\n{text}"


def format_transcript(title, author, duration_sec, chars, tags, text, out_md=None, timeout=120):
    """返回整理后的文案字符串；若给定 out_md，同时写入该文件。"""
    cfg = load_config()
    if not cfg.get("DEEPSEEK_API_KEY"):
        return _fallback(title, author, duration_sec, chars, tags, text, "未配置 DEEPSEEK_API_KEY")
    try:
        t0 = time.time()
        content = _call_deepseek(cfg, [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": build_prompt(title, author, duration_sec, chars, tags, text)},
        ], timeout)
        doc = _clean(content)
        if not doc.startswith("✅《"):
            raise RuntimeError("返回格式异常，无法定位标题")
        print(f"[format_doc] DeepSeek 整理完成，耗时 {time.time()-t0:.1f}s，{len(doc)} 字", file=sys.stderr)
    except Exception as e:
        return _fallback(title, author, duration_sec, chars, tags, text, f"{type(e).__name__}: {e}")

    if out_md:
        try:
            with open(out_md, "w", encoding="utf-8") as f:
                f.write(doc + "\n")
        except Exception as e:
            print(f"[format_doc] 写文件失败：{e}", file=sys.stderr)
    return doc


if __name__ == "__main__":
    # CLI：从 pipeline 的 result JSON 读取并格式化
    if len(sys.argv) < 2:
        print("用法: python3 format_doc.py '<pipeline result json>' [out_md]")
        sys.exit(1)
    r = json.loads(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else None
    print(format_transcript(
        r.get("title", ""), r.get("author", ""), r.get("duration", 0),
        r.get("chars", 0), r.get("tags", ""), r.get("text", ""), out_md=out,
    ))

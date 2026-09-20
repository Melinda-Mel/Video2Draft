#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_terms.py —— 专有名词纠错与巡检（本地、零延迟、确定性）。

为什么需要它（2026-09-20 她发现的问题）：
    转写把「Jev」听成「GV」、把「WorkBuddy」听成「Worker Body」。
    光靠 DeepSeek 的 GLOSSARY 提示是不够的——那只说"别改这些词"，
    **并不能把一个已经听错的结果改回来**，而且模型还可能把正确写法顺手"纠正"掉。

所以改成三层（本文件是第 1、2 层的实现，第 3 层在 format_doc.py）：

    L1 热词偏置：把词表喂给 whisper 的 initial_prompt，让它在解码时就倾向这些写法
                 （`hotwords_prompt()`，pipeline.transcribe 会带上）。
    L2 确定性纠错：转写完立刻做本地替换（`fix_segments()`，100% 可复现），三趟：
                 ① 变体表：TERMS / NAMES 里登记的「听错写法 → 正确写法」；
                 ② 写法归位：纯英文词条的大小写/空格/点号差异（Github→GitHub）；
                 ③ 近似归位：纯英文整词拼错（Kubernets→Kubernetes，相似度 ≥0.85）。
                 中文只走 ①——同音词太多，模糊匹配必然误伤正文。
    L3 提示兜底：DeepSeek 整理时再声明一遍「这些是正确写法，禁止改动」
                 （`prompt_lines()`，format_doc.py 直接引用，避免两处维护）。

**唯一维护点就是下面的 TERMS 表**：以后遇到新的听错，加一条 variants 即可，
不要再在别处重复维护词表（models_glossary.md 只是给人看的说明）。
"""
import re

# ---------- 唯一维护点：正确写法 ← 常见误听 ----------
# name: 正确写法（最终出现在稿子里的形态）
# aka:  并列说法/简称（只用于提示词，不会做替换）
# variants: whisper 常听成的样子（做替换；大小写/空格/点号差异由生成器自动容忍）
# desc: 给 DeepSeek 看的说明
TERMS = [
    {
        "name": "Jev",
        "aka": ["Jev 决策模型"],
        "desc": "团队自研的决策模型（System One / TypeSafe 里的 Jev 决策引擎），"
                "常出现在「用专精模型做判断、筛选、决策」的话题里",
        "variants": ["GV", "JV", "JEB", "G.V", "JE V"],
    },
    {
        "name": "WorkBuddy",
        "aka": ["WorkBuddy 桌面端"],
        "desc": "AI 工作台 / 智能体客户端（桌面 App）",
        "variants": ["WorkerBody", "WorkBody", "Workbuddy", "Worker Buddy", "WorkBuddy Desktop"],
    },
    {
        "name": "Hermes Agent",
        "aka": ["Hermes", "Hermès", "爱马仕"],
        "desc": "Nous Research 的开源 AI 智能体框架；国内昵称「爱马仕」，动词「养马」指部署/调教它",
        "variants": ["Hermis", "Hermez", "HermesAgent"],
    },
    {
        "name": "OpenClaw",
        "aka": ["龙虾", "小龙虾", "养虾"],
        "desc": "开源个人 AI 智能体框架，因红色龙虾图标得名，昵称「龙虾 / 小龙虾 / 养虾」",
        "variants": ["OpenClaw", "Openclaw", "OpenCraw", "Open Claw"],
    },
    {
        "name": "Claude",
        "aka": ["Claude Code"],
        "desc": "Anthropic 的 AI 模型（Claude / Claude Code）",
        "variants": ["Glock", "Cloth", "Cloude", "Clode", "Clawed"],
    },
    {
        "name": "Web Access",
        "aka": ["WebAccess", "网页操作 Skill"],
        "desc": "WorkBuddy 的「Web Access」Skill：让 WorkBuddy 替你操作网页（登录、填表单等）",
        "variants": ["WorkAsset", "WebAsset", "Work Access", "Web Access", "WebAccess", "Web Assess"],
    },
    {
        "name": "Find Skill",
        "aka": ["FindSkill"],
        "desc": "WorkBuddy 的「Find Skill」Skill：不知道装什么 Skill 时问它，会自动帮你找到对应的 Skill",
        "variants": ["FindSkil", "Find Scill", "Fined Skill"],
    },
    {
        "name": "Agent Rich",
        "aka": ["AgentRich"],
        "desc": "WorkBuddy 的「Agent Rich」Skill：帮 WorkBuddy 获取网上信息，找选题、找素材、做调研",
        "variants": ["Agent Witch", "AgentRich", "Agent Ridge", "Agent Wish", "Agent Richs"],
    },
    {
        "name": "Obsidian",
        "aka": ["Obsidian 笔记"],
        "desc": "本地 Markdown 笔记软件 Obsidian",
        "variants": ["Obsidain", "Obsidean", "Obsidion"],
    },
    {
        "name": "Seedance",
        "aka": ["Seedance 2.5", "豆包视频生成模型"],
        "desc": "字节跳动 Seed 团队出品的 AI 视频生成模型（上线豆包、即梦、扣子）",
        "variants": ["Seedence", "Seedance2.5", "SeaDance", "C Dance"],
    },
    {
        "name": "GrokBot",
        "aka": ["Grok Bot"],
        "desc": "xAI（马斯克旗下）的 AI 智能体产品 GrokBot，主打「数字员工」",
        "variants": ["Grogbot", "Grokbot", "Grog Bot", "Grok bot"],
    },
    # —— 下面是「通用错别字 / 术语」类，不限人物，任意位置都该改回来 ——
    {
        "name": "上链",
        "aka": ["上链（on-chain）"],
        "desc": "把资产 / 证券 / 货币登记到区块链上（on-chain），Web3 话题高频词",
        "variants": ["上裢", "上鏈"],
    },
    {
        "name": "RWA",
        "aka": ["Real World Assets", "真实世界资产"],
        "desc": "Real World Assets，把现实中的资产（房产、债券、货币等）代币化上链",
        "variants": [],
    },
    {
        "name": "赵长鹏",
        "aka": ["CZ", "币安创始人"],
        "desc": "币安（Binance）创始人赵长鹏，常用英文名 CZ",
        "variants": ["照长篷", "赵长蓬", "赵长朋"],
    },
    {
        "name": "孙宇晨",
        "aka": ["Justin Sun", "波场创始人"],
        "desc": "波场 TRON 创始人孙宇晨",
        "variants": ["孙宇辰", "孙雨晨"],
    },
    {
        "name": "比特币",
        "aka": ["Bitcoin", "BTC"],
        "desc": "最早的加密货币，2009 年由中本聪提出",
        "variants": ["比特幣", "比特逼", "比特B"],
    },
    {
        "name": "以太坊",
        "aka": ["Ethereum", "ETH"],
        "desc": "支持智能合约的公链，创始人 Vitalik Buterin（V 神）",
        "variants": ["以钛坊", "以太方"],
    },
    {
        "name": "币安",
        "aka": ["Binance"],
        "desc": "全球最大加密交易所，创始人赵长鹏（CZ）",
        "variants": ["必安", "币岸"],
    },
    {
        "name": "稳定币",
        "aka": ["Stablecoin"],
        "desc": "锚定法币的加密资产（USDT、USDC 等），跨境支付与 RWA 高频词",
        "variants": ["稳定幣"],
    },
    {
        "name": "Web3",
        "aka": ["第三代互联网"],
        "desc": "以区块链为底层的下一代互联网叙事",
        "variants": ["Web 3", "web3"],
    },
    {
        "name": "DeFi",
        "aka": ["去中心化金融"],
        "desc": "去中心化金融：链上借贷、交易、做市",
        "variants": ["Defi", "DEFI", "De FI"],
    },
    # —— 2026-09-20 她点名的第一优先级分类，挑「听错概率高 + 错了一读就不对」的进纠错层 ——
    {
        "name": "台积电",
        "aka": ["TSMC", "台湾积体电路制造"],
        "desc": "全球最大晶圆代工厂 TSMC，创始人张忠谋",
        "variants": ["台机电", "台積電", "台基电"],
    },
    {
        "name": "苏姿丰",
        "aka": ["Lisa Su", "AMD CEO"],
        "desc": "AMD 董事长 / CEO 苏姿丰（Lisa Su）",
        "variants": ["苏子丰", "苏姿峰"],
    },
    {
        "name": "张忠谋",
        "aka": ["Morris Chang"],
        "desc": "台积电创始人张忠谋",
        "variants": ["张中谋"],
    },
    {
        "name": "孙正义",
        "aka": ["Masayoshi Son"],
        "desc": "软银（SoftBank）创始人孙正义，愿景基金掌舵人",
        "variants": ["孙正毅", "孙正一"],
    },
    {
        "name": "千川",
        "aka": ["巨量千川"],
        "desc": "抖音的电商投流平台「巨量千川」",
        "variants": ["千穿", "仟川"],
    },
    {
        "name": "蒲公英",
        "aka": ["小红书蒲公英"],
        "desc": "小红书的品牌合作平台「蒲公英」",
        "variants": ["蒲公音", "普公英"],
    },
    {
        "name": "完播率",
        "aka": ["完播"],
        "desc": "短视频核心指标：看完视频的人数占比",
        "variants": ["完播律", "完播绿"],
    },
    {
        "name": "李尚龙",
        "aka": ["作家李尚龙"],
        "desc": "作家/前新东方老师李尚龙，常讲 AI 对职场与经济的影响（如解读 Anthropic 报告）",
        "variants": ["李生龙", "李尚籠", "李尚茏", "李尚泷"],
    },
]

# ---------- 参考词表（同样参与改写，不是只提示）----------
# 她 2026-09-20 要求：中美科技公司、创始人、模型名、以及各行业专有名词都收进来，
# 并且**要真的拿来改**，不能只是提示模型。
# 实际生效路径（见文件下方）：
#   · 英文/中英混排词条：自动做 大小写/空格 归位 + 拼写近似归位（覆盖面最大的一块）；
#   · 中文词条：可通过 variants 精确纠错（同音词太多，不做模糊匹配）；
#   · 全部词条同时作为 DeepSeek 白名单提示（prompt_lines("names") / names_glossary()）。
# **不进 whisper 热词**（initial_prompt 有 224 token 上限，三百多条塞不下，反而挤掉最该听对的词）。
# cat 只用于导出可读清单时的分组。新条目往对应分类里加即可。
NAMES = [
    # ===== 美国 =====
    {"name": "OpenAI", "aka": ["Open AI"], "cat": "美国",
     "desc": "美国 AI 公司；CEO Sam Altman（山姆·奥特曼），联合创始人 Greg Brockman（布罗克曼）、"
             "Ilya Sutskever；产品 ChatGPT、GPT-5、o 系列、Sora（视频）、DALL·E、Whisper、Codex"},
    {"name": "Sam Altman", "aka": ["山姆·奥特曼", "奥特曼"], "cat": "美国", "desc": "OpenAI 联合创始人 / CEO"},
    {"name": "Anthropic", "aka": [], "cat": "美国",
     "desc": "美国 AI 公司；创始人 / CEO Dario Amodei（达里奥·阿莫代伊），总裁 Daniela Amodei；产品 Claude、Claude Code"},
    {"name": "Dario Amodei", "aka": ["达里奥·阿莫代伊"], "cat": "美国", "desc": "Anthropic 联合创始人 / CEO"},
    {"name": "Google", "aka": ["谷歌", "Alphabet"], "cat": "美国",
     "desc": "CEO Sundar Pichai（桑达尔·皮查伊）；DeepMind CEO Demis Hassabis（哈萨比斯）；"
             "模型 Gemini、Veo、Imagen；产品 NotebookLM、AI Studio"},
    {"name": "Sundar Pichai", "aka": ["桑达尔·皮查伊", "皮查伊"], "cat": "美国", "desc": "Google / Alphabet CEO"},
    {"name": "Demis Hassabis", "aka": ["哈萨比斯"], "cat": "美国", "desc": "Google DeepMind CEO，2024 年诺贝尔化学奖"},
    {"name": "Microsoft", "aka": ["微软"], "cat": "美国",
     "desc": "CEO Satya Nadella（萨提亚·纳德拉），创始人 Bill Gates（比尔·盖茨）；产品 Copilot、Azure、GitHub Copilot"},
    {"name": "Satya Nadella", "aka": ["萨提亚·纳德拉", "纳德拉"], "cat": "美国", "desc": "微软 CEO"},
    {"name": "Meta", "aka": ["Facebook", "脸书"], "cat": "美国",
     "desc": "创始人 / CEO Mark Zuckerberg（马克·扎克伯格）；模型 Llama；首席 AI 科学家 Yann LeCun（杨立昆）"},
    {"name": "Mark Zuckerberg", "aka": ["马克·扎克伯格", "扎克伯格"], "cat": "美国", "desc": "Meta 创始人 / CEO"},
    {"name": "Apple", "aka": ["苹果"], "cat": "美国", "desc": "CEO Tim Cook（蒂姆·库克）；产品 Apple Intelligence、Siri"},
    {"name": "NVIDIA", "aka": ["英伟达"], "cat": "美国",
     "desc": "创始人 / CEO Jensen Huang（黄仁勋）；GPU（H100、B200）、CUDA、DGX"},
    {"name": "黄仁勋", "aka": ["Jensen Huang"], "cat": "美国", "desc": "英伟达创始人 / CEO"},
    {"name": "Tesla", "aka": ["特斯拉"], "cat": "美国", "desc": "CEO 马斯克；FSD 智驾、Optimus 人形机器人"},
    {"name": "SpaceX", "aka": ["太空探索技术公司"], "cat": "美国", "desc": "马斯克旗下航天公司；星链（Starlink）、星舰（Starship）"},
    {"name": "xAI", "aka": [], "cat": "美国", "desc": "马斯克旗下 AI 公司；模型 Grok（格罗克）、产品 GrokBot"},
    {"name": "Elon Musk", "aka": ["马斯克", "伊隆·马斯克"], "cat": "美国", "desc": "特斯拉 / SpaceX / xAI / X 掌舵人"},
    {"name": "Amazon", "aka": ["亚马逊"], "cat": "美国",
     "desc": "创始人 Jeff Bezos（贝索斯），现任 CEO Andy Jassy（安迪·贾西）；云 AWS、Bedrock、模型 Nova、助手 Alexa"},
    {"name": "Jeff Bezos", "aka": ["贝索斯"], "cat": "美国", "desc": "亚马逊创始人"},
    {"name": "Perplexity", "aka": [], "cat": "美国", "desc": "美国 AI 搜索引擎，CEO Aravind Srinivas"},
    {"name": "Midjourney", "aka": ["MJ"], "cat": "美国", "desc": "AI 绘画工具，创始人 David Holz"},
    {"name": "Cursor", "aka": ["Anysphere"], "cat": "美国", "desc": "AI 编程编辑器，CEO Michael Truell"},
    {"name": "Devin", "aka": ["Cognition"], "cat": "美国", "desc": "AI 程序员产品，出品方 Cognition，CEO Scott Wu"},
    {"name": "Hugging Face", "aka": [], "cat": "美国", "desc": "开源模型 / 数据集社区，CEO Clément Delangue"},
    {"name": "Figure", "aka": ["Figure AI"], "cat": "美国", "desc": "人形机器人公司，创始人 Brett Adcock"},
    {"name": "SSI", "aka": ["Safe Superintelligence"], "cat": "美国", "desc": "Ilya Sutskever 创办的 AI 安全公司"},
    {"name": "Ilya Sutskever", "aka": ["伊利亚·苏茨克维", "苏茨克维"], "cat": "美国", "desc": "OpenAI 前首席科学家、SSI 创始人"},
    {"name": "Mira Murati", "aka": ["米拉·穆拉蒂"], "cat": "美国", "desc": "OpenAI 前 CTO、Thinking Machines Lab 创始人"},
    {"name": "Andrej Karpathy", "aka": ["卡帕西"], "cat": "美国", "desc": "OpenAI 创始成员、特斯拉前 AI 负责人，AI 教学博主"},
    {"name": "Yann LeCun", "aka": ["杨立昆"], "cat": "美国", "desc": "Meta 首席 AI 科学家，图灵奖得主"},
    {"name": "Geoffrey Hinton", "aka": ["杰弗里·辛顿", "辛顿"], "cat": "美国", "desc": "深度学习之父，2024 年诺贝尔物理学奖"},
    {"name": "李飞飞", "aka": ["Fei-Fei Li"], "cat": "美国", "desc": "斯坦福教授、ImageNet 发起人、World Labs 创始人"},
    {"name": "Scale AI", "aka": [], "cat": "美国", "desc": "AI 数据标注公司，创始人 Alexandr Wang"},

    # ===== 中国 =====
    {"name": "字节跳动", "aka": ["ByteDance", "字节"], "cat": "中国",
     "desc": "创始人张一鸣，CEO 梁汝波；产品抖音 / TikTok、豆包（Doubao）、即梦（Dreamina）、扣子（Coze）、Trae；"
             "模型 Seed、Seedance（视频）"},
    {"name": "张一鸣", "aka": ["Zhang Yiming"], "cat": "中国", "desc": "字节跳动创始人"},
    {"name": "豆包", "aka": ["Doubao"], "cat": "中国", "desc": "字节跳动的 AI 助手 / 大模型"},
    {"name": "即梦", "aka": ["Dreamina"], "cat": "中国", "desc": "字节跳动的 AI 图片 / 视频生成产品"},
    {"name": "扣子", "aka": ["Coze"], "cat": "中国", "desc": "字节跳动的智能体（Bot）搭建平台"},
    {"name": "Trae", "aka": [], "cat": "中国", "desc": "字节跳动的 AI 编程 IDE"},
    {"name": "阿里巴巴", "aka": ["阿里", "Alibaba"], "cat": "中国",
     "desc": "创始人马云，董事局主席蔡崇信，CEO 吴泳铭；模型通义千问（Qwen）、通义万相（Wan）；产品夸克、阿里云"},
    {"name": "马云", "aka": ["Jack Ma"], "cat": "中国", "desc": "阿里巴巴创始人"},
    {"name": "通义千问", "aka": ["Qwen", "通义"], "cat": "中国", "desc": "阿里巴巴的大模型系列"},
    {"name": "腾讯", "aka": ["Tencent"], "cat": "中国",
     "desc": "创始人 / 董事会主席马化腾；产品微信（张小龙）、QQ、元宝；模型混元（Hunyuan）、WorkBuddy"},
    {"name": "马化腾", "aka": ["Pony Ma"], "cat": "中国", "desc": "腾讯创始人 / 董事会主席"},
    {"name": "张小龙", "aka": ["微信之父"], "cat": "中国", "desc": "微信创始人，腾讯高级副总裁"},
    {"name": "混元", "aka": ["Hunyuan"], "cat": "中国", "desc": "腾讯的大模型系列"},
    {"name": "百度", "aka": ["Baidu"], "cat": "中国", "desc": "创始人 / CEO 李彦宏；模型文心一言（ERNIE）；产品萝卜快跑（Apollo Go）"},
    {"name": "李彦宏", "aka": ["Robin Li"], "cat": "中国", "desc": "百度创始人 / CEO"},
    {"name": "文心一言", "aka": ["ERNIE", "文心"], "cat": "中国", "desc": "百度的大模型 / AI 助手"},
    {"name": "华为", "aka": ["Huawei"], "cat": "中国",
     "desc": "创始人任正非，终端业务余承东；模型盘古、芯片昇腾（Ascend）、系统鸿蒙（HarmonyOS）"},
    {"name": "任正非", "aka": [], "cat": "中国", "desc": "华为创始人"},
    {"name": "余承东", "aka": ["余大嘴"], "cat": "中国", "desc": "华为常务董事、终端 BG 董事长"},
    {"name": "DeepSeek", "aka": ["深度求索"], "cat": "中国", "desc": "创始人梁文锋；模型 DeepSeek-V3、DeepSeek-R1"},
    {"name": "梁文锋", "aka": [], "cat": "中国", "desc": "DeepSeek（深度求索）创始人"},
    {"name": "Kimi", "aka": ["月之暗面", "Moonshot"], "cat": "中国", "desc": "月之暗面的 AI 助手；创始人杨植麟"},
    {"name": "杨植麟", "aka": [], "cat": "中国", "desc": "月之暗面（Moonshot AI）创始人"},
    {"name": "智谱", "aka": ["Zhipu", "智谱 AI", "Z.ai"], "cat": "中国", "desc": "清华系 AI 公司，CEO 张鹏；模型 GLM、ChatGLM"},
    {"name": "MiniMax", "aka": ["海螺", "Hailuo"], "cat": "中国", "desc": "创始人闫俊杰；模型 abab、视频模型海螺、出海产品 Talkie"},
    {"name": "百川智能", "aka": ["Baichuan"], "cat": "中国", "desc": "创始人王小川；模型 Baichuan"},
    {"name": "王小川", "aka": [], "cat": "中国", "desc": "搜狗前 CEO、百川智能创始人"},
    {"name": "零一万物", "aka": ["01.AI"], "cat": "中国", "desc": "创始人李开复（Kai-Fu Lee）"},
    {"name": "李开复", "aka": ["Kai-Fu Lee"], "cat": "中国", "desc": "创新工场董事长、零一万物创始人"},
    {"name": "阶跃星辰", "aka": ["StepFun"], "cat": "中国", "desc": "创始人姜大昕；模型 Step 系列"},
    {"name": "商汤科技", "aka": ["商汤", "SenseTime"], "cat": "中国", "desc": "AI 视觉公司，CEO 徐立；模型日日新（SenseNova）"},
    {"name": "科大讯飞", "aka": ["讯飞", "iFlytek"], "cat": "中国", "desc": "语音 AI 公司，创始人刘庆峰；模型星火"},
    {"name": "小米", "aka": ["Xiaomi"], "cat": "中国", "desc": "创始人雷军；系统澎湃 OS、汽车 SU7、模型 MiMo"},
    {"name": "雷军", "aka": ["雷总"], "cat": "中国", "desc": "小米创始人 / CEO"},
    {"name": "美团", "aka": ["Meituan"], "cat": "中国", "desc": "创始人王兴；大模型 LongCat（龙猫）"},
    {"name": "王兴", "aka": [], "cat": "中国", "desc": "美团创始人 / CEO"},
    {"name": "京东", "aka": ["JD"], "cat": "中国", "desc": "创始人刘强东；大模型言犀"},
    {"name": "拼多多", "aka": ["PDD", "Temu"], "cat": "中国", "desc": "创始人黄峥，现任 CEO 陈磊"},
    {"name": "网易", "aka": ["NetEase"], "cat": "中国", "desc": "创始人丁磊"},
    {"name": "360", "aka": ["三六零", "红衣大叔"], "cat": "中国", "desc": "创始人周鸿祎；产品纳米 AI 搜索"},
    {"name": "快手", "aka": ["Kuaishou"], "cat": "中国", "desc": "创始人程一笑、宿华；视频生成模型可灵（Kling）"},
    {"name": "可灵", "aka": ["Kling"], "cat": "中国", "desc": "快手的 AI 视频生成模型"},
    {"name": "宇树科技", "aka": ["Unitree", "宇树"], "cat": "中国", "desc": "创始人王兴兴；四足 / 人形机器人（G1、H1）"},
    {"name": "大疆", "aka": ["DJI"], "cat": "中国", "desc": "创始人汪滔；无人机、云台 Osmo"},
    {"name": "智元机器人", "aka": ["AgiBot", "稚晖君"], "cat": "中国", "desc": "彭志辉（稚晖君）参与的具身智能公司"},
    {"name": "蔚来", "aka": ["NIO"], "cat": "中国", "desc": "创始人李斌；电动车 + 换电"},
    {"name": "理想汽车", "aka": ["理想", "Li Auto"], "cat": "中国", "desc": "创始人李想"},
    {"name": "小鹏汽车", "aka": ["小鹏", "XPeng"], "cat": "中国", "desc": "创始人何小鹏；智驾、机器人、飞行汽车"},
    {"name": "寒武纪", "aka": ["Cambricon"], "cat": "中国", "desc": "AI 芯片公司，创始人陈天石"},
    {"name": "摩尔线程", "aka": ["Moore Threads"], "cat": "中国", "desc": "国产 GPU 公司，创始人张建中"},
    {"name": "Vidu", "aka": ["生数科技"], "cat": "中国", "desc": "生数科技的 AI 视频生成模型"},
    {"name": "昆仑万维", "aka": ["天工"], "cat": "中国", "desc": "旗下天工大模型、SkyMusic 音乐模型"},
    {"name": "出门问问", "aka": ["Mobvoi"], "cat": "中国", "desc": "创始人李志飞；AIGC 产品「魔音工坊」"},

    # ===== 区块链 / Web3（她 2026-09-20 追加）=====
    {"name": "比特币", "aka": ["Bitcoin", "BTC"], "cat": "区块链",
     "desc": "最早的加密货币，2009 年由中本聪提出；交易所常见报价 BTC"},
    {"name": "中本聪", "aka": ["Satoshi Nakamoto"], "cat": "区块链", "desc": "比特币的匿名创始人"},
    {"name": "以太坊", "aka": ["Ethereum", "ETH"], "cat": "区块链",
     "desc": "支持智能合约的公链，发行 ETH；创始人 Vitalik Buterin（V 神）"},
    {"name": "Vitalik Buterin", "aka": ["V 神", "维塔利克"], "cat": "区块链", "desc": "以太坊联合创始人"},
    {"name": "币安", "aka": ["Binance", "BNB"], "cat": "区块链",
     "desc": "全球最大加密交易所；创始人赵长鹏（CZ），现任 CEO Richard Teng；平台币 BNB"},
    {"name": "Coinbase", "aka": ["COIN"], "cat": "区块链", "desc": "美国合规加密交易所，创始人 / CEO Brian Armstrong"},
    {"name": "波场", "aka": ["TRON", "TRX"], "cat": "区块链", "desc": "公链 TRON，创始人孙宇晨（Justin Sun）；稳定币 USDT 的最大发行链之一"},
    {"name": "Solana", "aka": ["SOL"], "cat": "区块链", "desc": "高性能公链 Solana，联合创始人 Anatoly Yakovenko"},
    {"name": "OKX", "aka": ["欧易"], "cat": "区块链", "desc": "加密交易所 OKX，创始人徐明星"},
    {"name": "Bybit", "aka": [], "cat": "区块链", "desc": "加密交易所 Bybit"},
    {"name": "Kraken", "aka": [], "cat": "区块链", "desc": "美国老牌加密交易所 Kraken"},
    {"name": "Tether", "aka": ["USDT", "泰达"], "cat": "区块链", "desc": "发行美元稳定币 USDT 的公司，CEO Paolo Ardoino"},
    {"name": "Circle", "aka": ["USDC"], "cat": "区块链", "desc": "发行美元稳定币 USDC 的公司，CEO Jeremy Allaire"},
    {"name": "稳定币", "aka": ["Stablecoin"], "cat": "区块链",
     "desc": "锚定法币的加密资产，主流有 USDT、USDC、USDe、DAI；常见于跨境支付与 RWA 话题"},
    {"name": "RWA", "aka": ["Real World Assets", "真实世界资产"], "cat": "区块链",
     "desc": "把现实资产（证券、债券、房产、货币）代币化上链，当前 Web3 最热赛道"},
    {"name": "上链", "aka": ["on-chain", "代币化"], "cat": "区块链", "desc": "把资产 / 数据登记到区块链上"},
    {"name": "DeFi", "aka": ["去中心化金融"], "cat": "区块链", "desc": "去中心化金融：链上借贷、交易、做市等"},
    {"name": "NFT", "aka": ["数字藏品"], "cat": "区块链", "desc": "非同质化代币，链上唯一凭证（数字艺术品、会员卡等）"},
    {"name": "DAO", "aka": ["去中心化自治组织"], "cat": "区块链", "desc": "用链上投票治理的组织形式"},
    {"name": "DePIN", "aka": [], "cat": "区块链", "desc": "去中心化物理基础设施网络（算力、带宽、存储等共享网络）"},
    {"name": "Layer 2", "aka": ["L2", "二层", "Rollup"], "cat": "区块链",
     "desc": "以太坊二层扩容方案，主流有 Arbitrum、Optimism、Base、zkSync、Starknet"},
    {"name": "Arbitrum", "aka": ["ARB"], "cat": "区块链", "desc": "以太坊二层网络（Rollup）"},
    {"name": "Base", "aka": [], "cat": "区块链", "desc": "Coinbase 推出的以太坊二层网络"},
    {"name": "Chainlink", "aka": ["LINK", "预言机"], "cat": "区块链", "desc": "链上预言机项目，把外部数据喂给智能合约"},
    {"name": "跨链桥", "aka": ["Bridge"], "cat": "区块链", "desc": "让资产在不同链之间转移的协议"},
    {"name": "DEX", "aka": ["去中心化交易所"], "cat": "区块链", "desc": "链上交易协议（Uniswap、PancakeSwap 等），与 CEX（中心化交易所）相对"},
    {"name": "Uniswap", "aka": ["UNI"], "cat": "区块链", "desc": "以太坊上最大的去中心化交易所"},
    {"name": "空投", "aka": ["Airdrop"], "cat": "区块链", "desc": "项目方向早期用户免费发放代币"},
    {"name": "质押", "aka": ["Staking"], "cat": "区块链", "desc": "锁定代币参与网络出块 / 获取收益"},
    {"name": "挖矿", "aka": ["Mining", "矿工"], "cat": "区块链", "desc": "用算力维护链并获取代币奖励"},
    {"name": "Gas 费", "aka": ["矿工费", "Gwei"], "cat": "区块链", "desc": "链上交易手续费，网络拥挤时飙升"},
    {"name": "私钥", "aka": ["助记词", "冷钱包", "热钱包"], "cat": "区块链",
     "desc": "私钥 / 助记词是资产唯一凭证；冷钱包离线保存更安全，热钱包联网更方便"},
    {"name": "TVL", "aka": ["总锁仓量"], "cat": "区块链", "desc": "Total Value Locked，协议里锁定的资产总额，DeFi 常用指标"},
    {"name": "MEV", "aka": [], "cat": "区块链", "desc": "最大可提取价值，矿工 / 验证者通过交易排序套利"},
    {"name": "Web3", "aka": ["第三代互联网"], "cat": "区块链", "desc": "以区块链为底层的下一代互联网叙事（去中心化身份、资产、应用）"},
    {"name": "Strategy", "aka": ["微策略", "MicroStrategy", "MSTR"], "cat": "区块链",
     "desc": "美国上市公司，执行主席 Michael Saylor，以大量持有比特币出名"},
    {"name": "Michael Saylor", "aka": ["塞勒"], "cat": "区块链", "desc": "Strategy（原 MicroStrategy）执行主席，比特币最大多头之一"},
    {"name": "贝莱德", "aka": ["BlackRock", "IBIT"], "cat": "区块链",
     "desc": "全球最大资管公司，CEO Larry Fink；其比特币现货 ETF（IBIT）规模居首"},
    {"name": "灰度", "aka": ["Grayscale", "GBTC"], "cat": "区块链", "desc": "老牌加密资产管理公司，比特币信托 GBTC 曾长期溢价"},
    {"name": "比特币现货 ETF", "aka": ["Spot Bitcoin ETF"], "cat": "区块链",
     "desc": "2024 年美国批准上市的比特币现货 ETF，传统资金进入加密的主要通道"},
    {"name": "香港证监会", "aka": ["SFC"], "cat": "区块链", "desc": "香港证券及期货事务监察委员会，虚拟资产交易平台（VATP）牌照的监管方"},
    {"name": "稳定币条例", "aka": ["香港稳定币条例"], "cat": "区块链",
     "desc": "香港 2025 年生效的稳定币发牌制度，是全球较早的稳定币专法"},
    {"name": "HashKey", "aka": ["HashKey Exchange"], "cat": "区块链", "desc": "香港持牌虚拟资产交易平台"},
    {"name": "OSL", "aka": [], "cat": "区块链", "desc": "香港持牌虚拟资产交易平台"},

    # ===== 芯片 / 算力（她 2026-09-20 追加：优先级最高，中英混排最易听错）=====
    {"name": "AMD", "aka": ["超威半导体", "超微"], "cat": "芯片", "desc": "美国芯片公司，CEO 苏姿丰（Lisa Su）；CPU 锐龙、GPU 镭龙（Radeon）、加速卡 MI 系列"},
    {"name": "Intel", "aka": ["英特尔"], "cat": "芯片", "desc": "美国芯片公司，前 CEO 帕特·基辛格，现任陈立武；CPU 酷睿（Core）、代工业务 Intel Foundry"},
    {"name": "台积电", "aka": ["TSMC", "台湾积体电路制造"], "cat": "芯片", "desc": "全球最大晶圆代工厂，创始人张忠谋"},
    {"name": "ASML", "aka": ["阿斯麦"], "cat": "芯片", "desc": "荷兰光刻机巨头，EUV 光刻机独家供应商"},
    {"name": "Arm", "aka": ["安谋", "ARM 架构"], "cat": "芯片", "desc": "英国芯片 IP 公司，移动端 CPU 架构垄断者"},
    {"name": "CUDA", "aka": [], "cat": "芯片", "desc": "英伟达的 GPU 并行计算平台，AI 训练的事实标准"},
    {"name": "GPU", "aka": ["显卡", "图形处理器"], "cat": "芯片", "desc": "图形处理器，AI 算力主力（H100、B200 等）"},
    {"name": "TPU", "aka": ["张量处理器"], "cat": "芯片", "desc": "Google 自研的 AI 加速芯片"},
    {"name": "NPU", "aka": ["神经网络处理器"], "cat": "芯片", "desc": "端侧 AI 加速单元（手机、PC 上跑本地模型）"},
    {"name": "HBM", "aka": ["高带宽内存"], "cat": "芯片", "desc": "AI 芯片配套的高带宽显存（HBM3E 等），SK 海力士 / 三星 / 美光供应"},
    {"name": "RISC-V", "aka": ["精简指令集"], "cat": "芯片", "desc": "开源指令集架构，国产芯片重要路线"},
    {"name": "CoWoS", "aka": ["先进封装"], "cat": "芯片", "desc": "台积电的 2.5D 先进封装技术，AI 芯片产能瓶颈"},
    {"name": "光刻机", "aka": ["EUV", "DUV"], "cat": "芯片", "desc": "芯片制造核心设备，先进制程用 EUV 光刻机"},
    {"name": "先进制程", "aka": ["3nm", "5nm", "2nm"], "cat": "芯片", "desc": "纳米级芯片工艺节点"},
    {"name": "黄仁勋", "aka": ["Jensen Huang"], "cat": "芯片", "desc": "英伟达创始人 / CEO"},
    {"name": "苏姿丰", "aka": ["Lisa Su"], "cat": "芯片", "desc": "AMD 董事长 / CEO"},
    {"name": "张忠谋", "aka": ["Morris Chang"], "cat": "芯片", "desc": "台积电创始人"},
    {"name": "高通", "aka": ["Qualcomm", "骁龙", "Snapdragon"], "cat": "芯片", "desc": "美国移动芯片公司，CEO 安蒙"},
    {"name": "博通", "aka": ["Broadcom"], "cat": "芯片", "desc": "美国芯片公司，CEO 陈福阳（Hock Tan），ASIC 定制芯片"},
    {"name": "联发科", "aka": ["MediaTek", "天玑"], "cat": "芯片", "desc": "台湾芯片公司，天玑（Dimensity）手机芯片"},
    {"name": "三星电子", "aka": ["Samsung"], "cat": "芯片", "desc": "韩国公司，存储芯片 / 晶圆代工 / 手机"},
    {"name": "SK 海力士", "aka": ["Hynix"], "cat": "芯片", "desc": "韩国存储芯片公司，HBM 主要供应商"},
    {"name": "美光", "aka": ["Micron"], "cat": "芯片", "desc": "美国存储芯片公司"},
    {"name": "中芯国际", "aka": ["SMIC"], "cat": "芯片", "desc": "中国大陆最大晶圆代工厂"},
    {"name": "海思", "aka": ["HiSilicon"], "cat": "芯片", "desc": "华为旗下芯片设计公司（麒麟、昇腾）"},
    {"name": "昇腾", "aka": ["Ascend"], "cat": "芯片", "desc": "华为的 AI 芯片 / 算力平台"},

    # ===== 开发工具 / 开源生态（她 2026-09-20 追加）=====
    {"name": "GitHub", "aka": [], "cat": "开发者", "desc": "全球最大代码托管平台（微软旗下），CEO Thomas Dohmke"},
    {"name": "GitLab", "aka": [], "cat": "开发者", "desc": "代码托管 / DevOps 平台"},
    {"name": "Git", "aka": [], "cat": "开发者", "desc": "分布式版本控制工具，作者 Linus Torvalds"},
    {"name": "Docker", "aka": ["容器"], "cat": "开发者", "desc": "应用容器化工具"},
    {"name": "Kubernetes", "aka": ["K8s"], "cat": "开发者", "desc": "容器编排系统（Google 开源）"},
    {"name": "Linux", "aka": [], "cat": "开发者", "desc": "开源操作系统内核，作者 Linus Torvalds"},
    {"name": "Python", "aka": [], "cat": "开发者", "desc": "最主流的 AI / 数据编程语言"},
    {"name": "JavaScript", "aka": ["JS"], "cat": "开发者", "desc": "前端 / 全栈编程语言"},
    {"name": "TypeScript", "aka": ["TS"], "cat": "开发者", "desc": "JavaScript 的类型化超集（微软出品）"},
    {"name": "Rust", "aka": [], "cat": "开发者", "desc": "系统级编程语言，以内存安全著称"},
    {"name": "API", "aka": ["接口"], "cat": "开发者", "desc": "应用程序接口，模型/服务调用的入口"},
    {"name": "SDK", "aka": ["开发包"], "cat": "开发者", "desc": "Software Development Kit，开发者工具包"},
    {"name": "CLI", "aka": ["命令行"], "cat": "开发者", "desc": "Command Line Interface，命令行工具"},
    {"name": "MCP", "aka": ["Model Context Protocol"], "cat": "开发者", "desc": "Anthropic 提出的模型上下文协议，AI 接外部工具的标准"},
    {"name": "Webhook", "aka": ["回调地址"], "cat": "开发者", "desc": "事件触发的 HTTP 回调"},
    {"name": "Codex", "aka": [], "cat": "开发者", "desc": "OpenAI 的 AI 编程智能体 / 编程模型"},
    {"name": "Vercel", "aka": [], "cat": "开发者", "desc": "前端部署平台，Next.js 出品方，CEO Guillermo Rauch"},
    {"name": "Supabase", "aka": [], "cat": "开发者", "desc": "开源版 Firebase，后端即服务"},
    {"name": "Agent", "aka": ["智能体"], "cat": "开发者", "desc": "能自主规划、调用工具完成任务的 AI 智能体"},
    {"name": "Workflow", "aka": ["工作流"], "cat": "开发者", "desc": "把多个步骤/工具串起来的自动化流程"},
    {"name": "RAG", "aka": ["检索增强生成"], "cat": "开发者", "desc": "Retrieval-Augmented Generation，先检索知识库再让模型回答"},
    {"name": "Embedding", "aka": ["向量化"], "cat": "开发者", "desc": "把文本转成向量，用于检索 / 聚类"},
    {"name": "向量数据库", "aka": ["Vector DB", "Pinecone", "Milvus"], "cat": "开发者", "desc": "存向量、做相似度检索的数据库"},
    {"name": "n8n", "aka": [], "cat": "开发者", "desc": "开源自动化工作流工具（Zapier 的开源替代）"},
    {"name": "Dify", "aka": [], "cat": "开发者", "desc": "开源 LLM 应用开发平台"},
    {"name": "ComfyUI", "aka": [], "cat": "开发者", "desc": "节点式 AI 绘画工作流工具（Stable Diffusion 生态）"},
    {"name": "LangChain", "aka": [], "cat": "开发者", "desc": "LLM 应用开发框架"},
    {"name": "Ollama", "aka": [], "cat": "开发者", "desc": "本地一键跑开源大模型的工具"},
    {"name": "vLLM", "aka": [], "cat": "开发者", "desc": "高吞吐的大模型推理框架"},
    {"name": "Whisper", "aka": [], "cat": "开发者", "desc": "OpenAI 的开源语音识别模型"},
    {"name": "FFmpeg", "aka": [], "cat": "开发者", "desc": "音视频处理命令行工具"},
    {"name": "Node.js", "aka": ["npm"], "cat": "开发者", "desc": "JavaScript 服务端运行时"},
    {"name": "PostgreSQL", "aka": ["Postgres"], "cat": "开发者", "desc": "开源关系型数据库"},
    {"name": "Redis", "aka": [], "cat": "开发者", "desc": "内存数据库 / 缓存"},
    {"name": "MongoDB", "aka": [], "cat": "开发者", "desc": "文档型数据库"},
    {"name": "VS Code", "aka": ["Visual Studio Code"], "cat": "开发者", "desc": "微软的开源代码编辑器"},
    {"name": "Postman", "aka": [], "cat": "开发者", "desc": "API 调试工具"},
    {"name": "Serverless", "aka": ["无服务器"], "cat": "开发者", "desc": "按调用计费的函数计算架构"},
    {"name": "DevOps", "aka": ["CI/CD"], "cat": "开发者", "desc": "开发运维一体化，含持续集成 / 持续交付"},

    # ===== 商业 / 创业 / 投融资（她 2026-09-20 追加）=====
    {"name": "VC", "aka": ["风险投资"], "cat": "创投", "desc": "Venture Capital，早期股权投资"},
    {"name": "PE", "aka": ["私募股权"], "cat": "创投", "desc": "Private Equity，私募股权 / 成长期投资"},
    {"name": "天使轮", "aka": ["Angel"], "cat": "创投", "desc": "最早的融资轮次"},
    {"name": "种子轮", "aka": ["Seed"], "cat": "创投", "desc": "天使之后、A 轮之前的融资"},
    {"name": "A 轮", "aka": ["B 轮", "C 轮", "Pre-A"], "cat": "创投", "desc": "机构化融资轮次"},
    {"name": "LP", "aka": ["有限合伙人"], "cat": "创投", "desc": "出钱的基金投资人"},
    {"name": "GP", "aka": ["普通合伙人"], "cat": "创投", "desc": "管钱、做决策的基金管理人"},
    {"name": "估值", "aka": ["Valuation"], "cat": "创投", "desc": "公司值多少钱（Pre-money / Post-money）"},
    {"name": "股权", "aka": ["期权", "ESOP"], "cat": "创投", "desc": "股份与员工期权激励"},
    {"name": "ARR", "aka": ["年度经常性收入"], "cat": "创投", "desc": "Annual Recurring Revenue，SaaS 核心指标"},
    {"name": "MRR", "aka": ["月度经常性收入"], "cat": "创投", "desc": "Monthly Recurring Revenue"},
    {"name": "GMV", "aka": ["交易总额"], "cat": "创投", "desc": "Gross Merchandise Volume，平台成交总额"},
    {"name": "PMF", "aka": ["产品市场匹配"], "cat": "创投", "desc": "Product-Market Fit，产品与市场是否对得上"},
    {"name": "MVP", "aka": ["最小可行产品"], "cat": "创投", "desc": "Minimum Viable Product"},
    {"name": "单位经济模型", "aka": ["Unit Economics"], "cat": "创投", "desc": "单笔生意是否赚钱（收入减成本）"},
    {"name": "Cap Table", "aka": ["股权结构表"], "cat": "创投", "desc": "公司股权分布表"},
    {"name": "Vesting", "aka": ["兑现期", "成熟期"], "cat": "创投", "desc": "股权 / 期权分期归属机制"},
    {"name": "Term Sheet", "aka": ["投资条款清单"], "cat": "创投", "desc": "投资的核心条款清单"},
    {"name": "尽调", "aka": ["DD", "尽职调查"], "cat": "创投", "desc": "投资前的财务 / 法律 / 业务核查"},
    {"name": "FA", "aka": ["财务顾问"], "cat": "创投", "desc": "帮公司对接投资人、撮合融资的中介"},
    {"name": "BP", "aka": ["商业计划书"], "cat": "创投", "desc": "Business Plan，融资用的计划书"},
    {"name": "路演", "aka": ["Roadshow"], "cat": "创投", "desc": "向投资人集中演示 / 讲项目的环节"},
    {"name": "对赌", "aka": ["业绩承诺"], "cat": "创投", "desc": "业绩不达标即触发补偿的条款"},
    {"name": "IPO", "aka": ["上市"], "cat": "创投", "desc": "首次公开发行"},
    {"name": "并购", "aka": ["M&A"], "cat": "创投", "desc": "Mergers & Acquisitions"},
    {"name": "退出", "aka": ["Exit"], "cat": "创投", "desc": "投资人把股份变现（上市 / 并购 / 回购）"},
    {"name": "独角兽", "aka": ["Unicorn"], "cat": "创投", "desc": "估值超 10 亿美元的未上市公司"},
    {"name": "红杉", "aka": ["Sequoia", "红杉资本"], "cat": "创投", "desc": "全球顶级 VC，曾投苹果、谷歌、字节跳动"},
    {"name": "YC", "aka": ["Y Combinator"], "cat": "创投", "desc": "美国最著名创业孵化器，创始人 Paul Graham"},
    {"name": "a16z", "aka": ["Andreessen Horowitz"], "cat": "创投", "desc": "硅谷顶级 VC，创始人马克·安德森"},
    {"name": "Benchmark", "aka": [], "cat": "创投", "desc": "硅谷老牌 VC"},
    {"name": "软银", "aka": ["SoftBank"], "cat": "创投", "desc": "日本投资集团，创始人孙正义；愿景基金（Vision Fund）"},
    {"name": "孙正义", "aka": ["Masayoshi Son"], "cat": "创投", "desc": "软银创始人，愿景基金掌舵人"},
    {"name": "彼得·蒂尔", "aka": ["Peter Thiel"], "cat": "创投", "desc": "PayPal 联合创始人、Founders Fund 合伙人，《从 0 到 1》作者"},
    {"name": "马克·安德森", "aka": ["Marc Andreessen"], "cat": "创投", "desc": "网景创始人、a16z 联合创始人"},
    {"name": "保罗·格雷厄姆", "aka": ["Paul Graham", "PG"], "cat": "创投", "desc": "YC 联合创始人，写创业随笔"},
    {"name": "巴菲特", "aka": ["Warren Buffett", "股神"], "cat": "创投", "desc": "伯克希尔·哈撒韦董事长，价值投资代表"},

    # ===== 产品 / 增长 / 市场营销（她 2026-09-20 追加：与她本职最相关）=====
    {"name": "DAU", "aka": ["日活"], "cat": "增长", "desc": "日活跃用户数"},
    {"name": "MAU", "aka": ["月活"], "cat": "增长", "desc": "月活跃用户数"},
    {"name": "ARPU", "aka": ["单用户收入"], "cat": "增长", "desc": "Average Revenue Per User"},
    {"name": "留存率", "aka": ["留存", "Retention"], "cat": "增长", "desc": "次日 / 7 日 / 30 日留存"},
    {"name": "复购率", "aka": ["复购"], "cat": "增长", "desc": "用户重复购买的比例"},
    {"name": "CAC", "aka": ["获客成本"], "cat": "增长", "desc": "Customer Acquisition Cost"},
    {"name": "LTV", "aka": ["用户生命周期价值"], "cat": "增长", "desc": "Life Time Value，常与 CAC 对比看是否赚钱"},
    {"name": "ROI", "aka": ["投资回报率"], "cat": "增长", "desc": "Return on Investment"},
    {"name": "ROAS", "aka": ["广告支出回报"], "cat": "增长", "desc": "Return on Ad Spend，投放核心指标"},
    {"name": "CTR", "aka": ["点击率"], "cat": "增长", "desc": "Click Through Rate"},
    {"name": "CVR", "aka": ["转化率"], "cat": "增长", "desc": "Conversion Rate"},
    {"name": "CPA", "aka": ["单次转化成本"], "cat": "增长", "desc": "Cost Per Action"},
    {"name": "CPC", "aka": ["单次点击成本"], "cat": "增长", "desc": "Cost Per Click"},
    {"name": "CPM", "aka": ["千次曝光成本"], "cat": "增长", "desc": "Cost Per Mille"},
    {"name": "AARRR", "aka": ["海盗指标"], "cat": "增长", "desc": "获取 / 激活 / 留存 / 变现 / 推荐 五段式增长模型"},
    {"name": "增长飞轮", "aka": ["Flywheel"], "cat": "增长", "desc": "自我强化的增长循环"},
    {"name": "裂变", "aka": ["推荐拉新"], "cat": "增长", "desc": "老带新的增长玩法"},
    {"name": "归因", "aka": ["Attribution"], "cat": "增长", "desc": "把转化效果归到具体渠道"},
    {"name": "SEO", "aka": ["搜索引擎优化"], "cat": "增长", "desc": "Search Engine Optimization"},
    {"name": "SEM", "aka": ["搜索竞价"], "cat": "增长", "desc": "Search Engine Marketing"},
    {"name": "ASO", "aka": ["应用商店优化"], "cat": "增长", "desc": "App Store Optimization"},
    {"name": "信息流投放", "aka": ["信息流广告"], "cat": "增长", "desc": "抖音 / 朋友圈等平台的推荐流广告"},
    {"name": "KOL", "aka": ["关键意见领袖", "达人"], "cat": "增长", "desc": "头部内容创作者 / 达人"},
    {"name": "KOC", "aka": ["关键意见消费者"], "cat": "增长", "desc": "更小、更真实的素人创作者"},
    {"name": "UGC", "aka": ["用户生成内容"], "cat": "增长", "desc": "User Generated Content"},
    {"name": "PGC", "aka": ["专业生成内容"], "cat": "增长", "desc": "Professionally Generated Content"},
    {"name": "DTC", "aka": ["独立站", "品牌直营"], "cat": "增长", "desc": "Direct to Consumer，不依赖平台的自营模式"},
    {"name": "私域", "aka": ["私域流量"], "cat": "增长", "desc": "可直接触达的用户池（社群、企微等）"},
    {"name": "公域", "aka": ["公域流量"], "cat": "增长", "desc": "平台推荐分发的流量"},
    {"name": "种草", "aka": ["拔草"], "cat": "增长", "desc": "小红书语境：被内容安利 = 种草，最终下单 = 拔草"},
    {"name": "转化漏斗", "aka": ["漏斗"], "cat": "增长", "desc": "从曝光到成交的逐层流失模型"},
    {"name": "冷启动", "aka": ["Cold Start"], "cat": "增长", "desc": "新账号 / 新功能零数据起步阶段"},
    {"name": "用户画像", "aka": ["Persona"], "cat": "增长", "desc": "目标用户的特征标签合集"},
    {"name": "需求验证", "aka": ["PMF 验证"], "cat": "增长", "desc": "确认用户是否为这个需求付费"},
    {"name": "灰度发布", "aka": ["A/B 测试", "AB Test"], "cat": "增长", "desc": "小流量先上，验证后再全量"},
    {"name": "蒲公英", "aka": ["小红书蒲公英"], "cat": "增长", "desc": "小红书的品牌合作（报备）平台"},
    {"name": "星图", "aka": ["巨量星图"], "cat": "增长", "desc": "抖音的达人商业合作平台"},
    {"name": "千川", "aka": ["巨量千川"], "cat": "增长", "desc": "抖音电商投流平台"},
    {"name": "DOU+", "aka": ["抖加"], "cat": "增长", "desc": "抖音的内容加热（投流）工具"},
    {"name": "薯条", "aka": [], "cat": "增长", "desc": "小红书的内容加热工具"},
    {"name": "投流", "aka": ["投放"], "cat": "增长", "desc": "付费买流量"},
    {"name": "完播率", "aka": ["完播"], "cat": "增长", "desc": "短视频核心指标：看完的人占比"},
    {"name": "人设", "aka": ["账号定位"], "cat": "增长", "desc": "账号对外呈现的人物设定"},
    {"name": "账号矩阵", "aka": ["矩阵号"], "cat": "增长", "desc": "一人 / 一机构批量运营多个账号"},
    {"name": "MCN", "aka": ["网红经纪"], "cat": "增长", "desc": "Multi-Channel Network，达人孵化 / 经纪机构"},
    {"name": "货架电商", "aka": ["商城"], "cat": "增长", "desc": "靠搜索和商城成交，与内容电商相对"},
    {"name": "直播切片", "aka": ["切片"], "cat": "增长", "desc": "把直播高光剪成短视频分发"},
    {"name": "带货", "aka": ["直播带货"], "cat": "增长", "desc": "内容里挂链接卖货"},
    {"name": "选题池", "aka": ["选题库"], "cat": "增长", "desc": "储备待做的内容选题清单"},
    {"name": "钩子", "aka": ["Hook"], "cat": "增长", "desc": "开头几秒抓住观众的点"},
    {"name": "UV", "aka": ["PV"], "cat": "增长", "desc": "独立访客数 / 页面浏览量"},

    # ===== 互联网平台 / 应用 / 内容生态（她 2026-09-20 追加）=====
    {"name": "小红书", "aka": ["RED", "rednote"], "cat": "平台", "desc": "种草社区，创始人毛文超、瞿芳"},
    {"name": "抖音", "aka": ["Douyin"], "cat": "平台", "desc": "字节跳动的短视频平台（海外版 TikTok）"},
    {"name": "TikTok", "aka": ["海外版抖音"], "cat": "平台", "desc": "字节跳动的海外短视频平台，CEO 周受资（Shou Zi Chew）"},
    {"name": "视频号", "aka": ["微信视频号", "Channels"], "cat": "平台", "desc": "微信内的短视频 / 直播功能"},
    {"name": "B 站", "aka": ["哔哩哔哩", "Bilibili"], "cat": "平台", "desc": "中长视频社区，CEO 陈睿"},
    {"name": "YouTube", "aka": ["油管"], "cat": "平台", "desc": "Google 旗下的全球视频平台"},
    {"name": "X", "aka": ["Twitter", "推特"], "cat": "平台", "desc": "马斯克收购的社交平台（原 Twitter）"},
    {"name": "Reddit", "aka": [], "cat": "平台", "desc": "美国论坛式社区"},
    {"name": "Discord", "aka": [], "cat": "平台", "desc": "社群 / 语音社区工具，AI 圈子常用"},
    {"name": "Telegram", "aka": ["电报"], "cat": "平台", "desc": "加密通讯 / 频道工具，创始人 Pavel Durov"},
    {"name": "LinkedIn", "aka": ["领英"], "cat": "平台", "desc": "职场社交平台（微软旗下）"},
    {"name": "Instagram", "aka": ["IG", "ins"], "cat": "平台", "desc": "Meta 旗下的图片社交平台"},
    {"name": "WhatsApp", "aka": [], "cat": "平台", "desc": "Meta 旗下的全球即时通讯工具"},
    {"name": "Snapchat", "aka": [], "cat": "平台", "desc": "美国阅后即焚社交 App"},
    {"name": "Pinterest", "aka": [], "cat": "平台", "desc": "图片灵感收集社区"},
    {"name": "Twitch", "aka": [], "cat": "平台", "desc": "游戏直播平台（亚马逊旗下）"},
    {"name": "Netflix", "aka": ["奈飞"], "cat": "平台", "desc": "全球流媒体巨头"},
    {"name": "Spotify", "aka": [], "cat": "平台", "desc": "全球音乐流媒体平台"},
    {"name": "Substack", "aka": [], "cat": "平台", "desc": "付费订阅通讯（Newsletter）平台"},
    {"name": "Medium", "aka": [], "cat": "平台", "desc": "英文博客 / 写作平台"},
    {"name": "Shopify", "aka": [], "cat": "平台", "desc": "全球最大独立站建站平台"},
    {"name": "Temu", "aka": ["拼多多海外版"], "cat": "平台", "desc": "拼多多的跨境电商平台"},
    {"name": "SHEIN", "aka": ["希音"], "cat": "平台", "desc": "快时尚跨境独立站"},
    {"name": "Amazon", "aka": ["亚马逊"], "cat": "平台", "desc": "全球最大电商平台，也做云（AWS）"},
    {"name": "飞书", "aka": ["Lark", "Feishu"], "cat": "平台", "desc": "字节跳动的协作办公套件"},
    {"name": "钉钉", "aka": ["DingTalk"], "cat": "平台", "desc": "阿里巴巴的企业协同办公平台"},
    {"name": "企业微信", "aka": ["企微"], "cat": "平台", "desc": "腾讯面向企业的办公 / 客户运营工具"},
    {"name": "Notion", "aka": [], "cat": "平台", "desc": "笔记 / 文档 / 知识库协作工具"},
    {"name": "App Store", "aka": ["应用商店"], "cat": "平台", "desc": "苹果的应用分发商店"},
    {"name": "Google Play", "aka": ["谷歌商店"], "cat": "平台", "desc": "安卓的应用分发商店"},
    {"name": "知乎", "aka": ["Zhihu"], "cat": "平台", "desc": "中文问答社区"},
    {"name": "微博", "aka": ["Weibo"], "cat": "平台", "desc": "中文社交媒体平台"},
    {"name": "微信公众号", "aka": ["公号"], "cat": "平台", "desc": "微信内的图文内容生态"},
    {"name": "小宇宙", "aka": [], "cat": "平台", "desc": "中文播客 App"},
    {"name": "播客", "aka": ["Podcast"], "cat": "平台", "desc": "音频节目形态"},
    {"name": "即刻", "aka": ["Jike"], "cat": "平台", "desc": "中文兴趣社区 / 社交 App"},
    {"name": "豆瓣", "aka": ["Douban"], "cat": "平台", "desc": "中文书影音评分社区"},
    {"name": "Alphabet", "aka": ["Google 母公司"], "cat": "平台", "desc": "Google 的母公司"},

    # ===== 金融 / 宏观经济 / 投资（她 2026-09-20 追加）=====
    {"name": "美联储", "aka": ["Fed", "Federal Reserve"], "cat": "金融", "desc": "美国中央银行，主席 Jerome Powell（鲍威尔）"},
    {"name": "鲍威尔", "aka": ["Jerome Powell"], "cat": "金融", "desc": "美联储主席"},
    {"name": "欧洲央行", "aka": ["ECB"], "cat": "金融", "desc": "欧元区中央银行"},
    {"name": "降息", "aka": ["加息", "利率决议"], "cat": "金融", "desc": "央行调整基准利率，全球资产定价的锚"},
    {"name": "量化宽松", "aka": ["QE", "缩表"], "cat": "金融", "desc": "央行印钱买资产 / 反向缩表"},
    {"name": "CPI", "aka": ["消费者物价指数"], "cat": "金融", "desc": "通胀核心指标"},
    {"name": "PPI", "aka": ["生产者物价指数"], "cat": "金融", "desc": "工业品出厂价格指数"},
    {"name": "GDP", "aka": ["国内生产总值"], "cat": "金融", "desc": "经济总量指标"},
    {"name": "PMI", "aka": ["采购经理指数"], "cat": "金融", "desc": "荣枯线 50，看经济冷热"},
    {"name": "纳斯达克", "aka": ["Nasdaq", "纳指"], "cat": "金融", "desc": "美国科技股为主的交易所 / 指数"},
    {"name": "标普 500", "aka": ["S&P 500"], "cat": "金融", "desc": "美国大盘指数"},
    {"name": "道琼斯", "aka": ["Dow Jones"], "cat": "金融", "desc": "美国老牌指数"},
    {"name": "恒生指数", "aka": ["恒指", "HSI"], "cat": "金融", "desc": "香港股市基准指数"},
    {"name": "A 股", "aka": ["上证指数", "创业板", "科创板"], "cat": "金融", "desc": "中国内地股市"},
    {"name": "ETF", "aka": ["交易型开放式指数基金"], "cat": "金融", "desc": "可像股票一样买卖的指数基金"},
    {"name": "期权", "aka": ["Options"], "cat": "金融", "desc": "到期行权的衍生品合约"},
    {"name": "做空", "aka": ["空头"], "cat": "金融", "desc": "赌下跌的仓位"},
    {"name": "对冲", "aka": ["Hedge"], "cat": "金融", "desc": "用反向仓位降低风险"},
    {"name": "回撤", "aka": ["Drawdown"], "cat": "金融", "desc": "从高点到低点的最大跌幅"},
    {"name": "市盈率", "aka": ["PE 倍数", "P/E"], "cat": "金融", "desc": "股价 / 每股收益，最常用估值指标"},
    {"name": "自由现金流", "aka": ["FCF"], "cat": "金融", "desc": "Free Cash Flow，企业真正能自由支配的现金"},
    {"name": "财报", "aka": ["季报", "年报", "Earnings"], "cat": "金融", "desc": "上市公司定期业绩披露"},
    {"name": "国债收益率", "aka": ["美债", "10 年期美债"], "cat": "金融", "desc": "全球资产定价之锚"},
    {"name": "美元指数", "aka": ["DXY"], "cat": "金融", "desc": "美元对一篮子货币的强弱"},
    {"name": "NVDA", "aka": ["英伟达股票代码"], "cat": "金融", "desc": "英伟达（NVIDIA）在纳斯达克的代码"},
    {"name": "TSLA", "aka": ["特斯拉股票代码"], "cat": "金融", "desc": "特斯拉（Tesla）的代码"},
    {"name": "AAPL", "aka": ["苹果股票代码"], "cat": "金融", "desc": "苹果（Apple）的代码"},
    {"name": "MSFT", "aka": ["微软股票代码"], "cat": "金融", "desc": "微软（Microsoft）的代码"},
    {"name": "GOOGL", "aka": ["谷歌股票代码"], "cat": "金融", "desc": "Alphabet（Google）的代码"},
    {"name": "AMZN", "aka": ["亚马逊股票代码"], "cat": "金融", "desc": "亚马逊（Amazon）的代码"},
    {"name": "META", "aka": ["Meta 股票代码"], "cat": "金融", "desc": "Meta 的代码"},
    {"name": "TSM", "aka": ["台积电股票代码"], "cat": "金融", "desc": "台积电（TSMC）在美股的代码"},

    # ===== 模型 / 产品（跨国通用）=====
    {"name": "ChatGPT", "aka": ["Chat GPT", "GPT"], "cat": "模型", "desc": "OpenAI 的对话产品（底层 GPT-5 / o 系列）"},
    {"name": "Gemini", "aka": ["双子星"], "cat": "模型", "desc": "Google 的大模型 / AI 助手"},
    {"name": "Llama", "aka": ["羊驼"], "cat": "模型", "desc": "Meta 的开源大模型"},
    {"name": "Grok", "aka": [], "cat": "模型", "desc": "xAI 的大模型 / AI 助手"},
    {"name": "Sora", "aka": [], "cat": "模型", "desc": "OpenAI 的视频生成模型"},
    {"name": "Veo", "aka": [], "cat": "模型", "desc": "Google 的视频生成模型"},
    {"name": "Nano Banana", "aka": ["纳米香蕉"], "cat": "模型", "desc": "Google 的图片生成 / 编辑模型"},
    {"name": "Flux", "aka": ["FLUX.1"], "cat": "模型", "desc": "Black Forest Labs 的开源图片生成模型"},
    {"name": "Stable Diffusion", "aka": ["SD", "Stability AI"], "cat": "模型", "desc": "Stability AI 的开源图片生成模型"},
    {"name": "Manus", "aka": [], "cat": "模型", "desc": "中国团队 Monica 推出的通用 AI Agent"},
    {"name": "Mistral", "aka": ["Mistral AI"], "cat": "模型", "desc": "法国 AI 公司，CEO Arthur Mensch；模型 Mistral、Le Chat"},
]

# 巡检白名单：这些大写词是正常的英文，不要报成「可疑词」
STOPWORDS = {
    "AI", "API", "APIS", "OK", "GPT", "GLM", "LLM", "ID", "K", "KEY", "URL", "APP", "SDK",
    "CPU", "GPU", "MP4", "MOV", "JSON", "PPT", "PDF", "CSV", "TOKEN", "AGENT", "SKILL", "SKILLS",
    "WORKFLOW", "PROMPT", "CLI", "UI", "UX", "WIFI", "IP", "MAC", "PC", "MACOS", "IOS",
    "TIKTOK", "YOUTUBE", "BILIBILI", "GITHUB", "TWITTER", "HOT", "RSS", "MCP", "CRM", "KPI",
    "C", "V", "B", "X", "S", "P", "N", "D", "T", "R", "M", "W", "A", "E", "F", "G", "H", "I",
    "J", "L", "O", "Q", "U", "Y", "Z",
}


def _variant_re(v):
    """把一个误听变体编译成抗空格/点号/连字符的宽松正则。

    例：「WorkerBody」→ 能同时命中 WorkerBody / Worker Body / worker-body / Worker.Body
    纯英文变体才加 ASCII 边界（防止匹配到单词内部）；
    含汉字的变体**不能**加——否则「CC照长篷」这种「英文紧挨中文」的写法就漏了（2026-09-20 实测踩过）。
    """
    chars = [re.escape(c) for c in v if not c.isspace()]
    body = r"[\s\-_.]{0,2}".join(chars)
    ascii_only = all(c.isascii() for c in v)
    if not ascii_only:
        return re.compile(body)
    return re.compile(r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])", re.IGNORECASE)


_RULES = []          # [(regex, 正确写法)]：变体 → 正确写法（TERMS 和 NAMES 都支持 variants）
for _t in TERMS + NAMES:
    for _v in _t.get("variants", []):
        if _v.lower().strip() == _t["name"].lower().strip():
            continue                      # 本来就是对的不必替换
        _RULES.append((_variant_re(_v), _t["name"]))


# ---------- 参考词表的「自动归位」（她 2026-09-20：参考词也要替换，不能只提示）----------
# 中文名/公司名**不做**模糊匹配：同音词太多（「京东」和「惊动」完全同音），硬替换必然误伤正文。
# 中文这边靠 TERMS/NAMES 的 variants 精确替换（发现一处加一条）。
# 英文/中英混排不一样——机器听错基本都是「少个字母 / 多个字母 / 拼写近似 / 大小写乱」，
# 所以这里给纯英文词条加两层自动纠错：
#   ① 写法归位：容忍空格/点号/连字符的大小写写法 → 词条的标准写法（Github→GitHub、huggingface→Hugging Face）
#   ② 拼写近似：整词和标准写法相似度 ≥0.85 → 归位（Kubernets→Kubernetes、Gitub→GitHub）
import difflib  # noqa: E402

# 这些英文词本身是常用词，只做大小写归位容易误伤英文内容（她也会转英文视频），故不参与
_NO_CASE = {"arm", "base", "medium", "figure", "notion", "circle", "strategy"}
_FUZZ_MIN_LEN = 6      # 太短的词（PE/VC/GMV）只要大小写归位，不做模糊
_FUZZ_CUTOFF = 0.85


def _norm_key(s):
    """归一化比较键：去掉空格/点/连字符/下划线并转小写。"""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_ASCII_NAME_RULES, _FUZZ_TARGETS, _KNOWN_ASCII, _seen_keys = [], {}, set(), set()
for _t in TERMS + NAMES:
    _nm = _t["name"]
    for _a in [_nm] + list(_t.get("aka", [])):
        if _a and _a.isascii():
            _KNOWN_ASCII.add(_norm_key(_a))            # 已知写法（含别称）→ 不参与模糊
    if not _nm.isascii():
        continue
    _k = _norm_key(_nm)
    if len(_k) >= 2 and _k not in _NO_CASE and _k not in _seen_keys:
        _seen_keys.add(_k)
        _ASCII_NAME_RULES.append((_variant_re(_nm), _nm))   # ① 写法归位
    if len(_k) >= _FUZZ_MIN_LEN:
        _FUZZ_TARGETS.setdefault(_k, _nm)                   # ② 近似归位目标
_FUZZ_KEYS = list(_FUZZ_TARGETS)


def _fuzzy_fix(text):
    """英文整词的拼写近似归位（只认纯英文 token，绝不动中文）。返回 (新文字, 次数)。"""
    n = 0

    def rep(m):
        nonlocal n
        w = m.group(0)
        if len(_norm_key(w)) < _FUZZ_MIN_LEN or w.upper() in STOPWORDS:
            return w
        k = _norm_key(w)
        if k in _KNOWN_ASCII:                 # 是已知写法（包括别称）就用原样
            return w
        near = difflib.get_close_matches(k, _FUZZ_KEYS, n=1, cutoff=_FUZZ_CUTOFF)
        if near:
            n += 1
            return _FUZZ_TARGETS[near[0]]
        return w

    return re.sub(r"[A-Za-z][A-Za-z0-9.\-]{4,24}", rep, text), n


def _collapse_repeat(text):
    """同一短句在一段里连着刷 ≥6 遍（whisper 复读幻觉）→ 只留一遍。"""
    t = _LOOP_RE.sub(r"\1", text or "")
    # 再收一下「复读句 + 几个杂字（外语/杂音） + 同一句」这种尾巴
    return _LOOP_TAIL_RE.sub(r"\1", t)


_LOOP_RE = re.compile(r"([\u4e00-\u9fff]{2,12}[。！？])" + r"(?:[\s]*\1){5,}")
_LOOP_TAIL_RE = re.compile(r"([\u4e00-\u9fff]{2,12}[。！？])(?:[^\u4e00-\u9fff]{1,16}\1)+")


def fix_text(text):
    """按词表纠正一段文字，返回 (新文字, 纠正次数)。

    三趟：① 变体表（两层都算）② 英文写法归位 ③ 英文拼写近似归位，
    最后统一清理 whisper 的复读幻觉。
    """
    n = 0
    for rx, good in _RULES:
        text, k = rx.subn(good, text)
        n += k
    for rx, good in _ASCII_NAME_RULES:
        text, k = rx.subn(good, text)
        n += k
    text, k = _fuzzy_fix(text)
    n += k
    return _collapse_repeat(text), n


def fix_segments(segs):
    """就地纠正 segments 里的 text，返回总纠正次数。"""
    n = 0
    for s in segs or []:
        if s.get("text"):
            s["text"], k = fix_text(s["text"])
            n += k
    return n


def _cjk_only(s):
    """只留中日韩汉字，用于判「这两句是不是同一句」。"""
    return "".join(ch for ch in (s or "") if "\u4e00" <= ch <= "\u9fff")


def trim_loops(segs, min_run=4, max_len=24):
    """砍掉 whisper 在结尾纯音乐/卡点上产生的「复读」幻觉，返回 (保留的 segs, 丢弃数)。

    实测（2026-09-20《多略的功导》）：结尾把「走得来到来。」连吐 28 遍，中间还混进俄语。
    规则极保守：**连续 ≥min_run 段的汉字部分完全相同**、且这段不超过 max_len 字，才只留第一段。
    只要句子有一点不一样就不动——宁可漏删，不可乱删正文。
    """
    segs = list(segs or [])
    out, i, dropped = [], 0, 0
    while i < len(segs):
        key = _cjk_only(segs[i].get("text", ""))
        j = i + 1
        if key and len(key) <= max_len:
            while j < len(segs) and _cjk_only(segs[j].get("text", "")) == key:
                j += 1
            if j - i >= min_run:
                out.append(segs[i])
                dropped += j - i - 1
                i = j
                continue
        out.append(segs[i])
        i += 1
    return out, dropped


HOT_BUDGET = 200       # whisper initial_prompt 约 224 token，中文 1 字常占 1~2 token，留足余量


def hotwords(budget=HOT_BUDGET):
    """喂给 whisper initial_prompt 的热词。

    **只放 TERMS**（要纠错的那批），不放 NAMES：
    whisper 的 initial_prompt 有 ~224 token 的硬上限，一百多条公司/人名塞进去会被截断，
    反而把最该听对的那几个词挤掉。NAMES 走另一条路——DeepSeek 的白名单提示（prompt_lines），
    那里没有长度压力，效果也更好（模型会按正确写法改，而不是靠解码偏置）。

    取词顺序：**先所有正确写法**（保证 22 个核心词全进），再补别称 / 简称。
    """
    seen, res, used = set(), [], 0

    def take(w):
        nonlocal used
        w = (w or "").strip()
        if not w or w.lower() in seen:
            return
        if used + len(w) + 1 > budget:
            return
        seen.add(w.lower())
        res.append(w)
        used += len(w) + 1

    for t in TERMS:                    # 第一轮：全部正确写法
        take(t["name"])
    for t in TERMS:                    # 第二轮：还有余量再放别称
        for a in t.get("aka", []):
            take(a)
    return res


def hotwords_prompt(base="以下是普通话视频口播内容。"):
    """whisper 的 initial_prompt：base + 专有名词（长度由 hotwords 的预算控制）。"""
    return f"{base}专有名词：{'、'.join(hotwords())}。"


def prompt_lines(group="terms"):
    """给 DeepSeek 的「正确写法」清单，format_doc.GLOSSARY 直接用它（单一维护点）。

    group="terms" → 必须一字不改的那批；group="names" → 公司 / 创始人 / 模型参考词表；
    group="all"  → 两批都要。
    """
    src = {"terms": TERMS, "names": NAMES, "all": TERMS + NAMES}.get(group, TERMS)
    lines = []
    for t in src:
        names = " / ".join([t["name"]] + [a for a in t.get("aka", []) if a != t["name"]])
        lines.append((names, t["desc"]))
    return lines


CAT_ORDER = [
    "芯片", "开发者", "创投", "增长", "平台", "金融",
    "美国", "中国", "区块链", "模型",
]

CAT_TITLE = {
    "芯片": "芯片 / 算力", "开发者": "开发工具 / 开源生态", "创投": "创业 / 投融资",
    "增长": "产品 / 增长 / 营销", "平台": "平台 / 内容生态", "金融": "金融 / 宏观 / 投资",
    "美国": "美国科技公司 / 人物", "中国": "中国科技公司 / 人物",
    "区块链": "区块链 / Web3", "模型": "模型 / 产品",
}


def _name_with_aka(t):
    aka = [a for a in t.get("aka", []) if a and a != t["name"]]
    return t["name"] + ("（" + "/".join(aka) + "）" if aka else "")


def grouped_names():
    """按分类返回 [(分类, [词条…])]，已去重（同名只保留第一次出现）。"""
    seen, out = set(), []
    for c in CAT_ORDER:
        items = []
        for t in NAMES:
            if t.get("cat") != c:
                continue
            k = t["name"].lower()
            if k in seen:
                continue
            seen.add(k)
            items.append(t)
        if items:
            out.append((c, items))
    return out


def names_glossary():
    """给 DeepSeek 的**紧凑版**参考词表：只列「正确写法（别称/简写）」，一律不带释义。

    为什么压缩（她 2026-09-20 要求「文档尽可能压缩、读取别耗时」）：
    模型要做的事只有一件——看到听错的近似写法，改回这里的拼写。
    分类标题已经提供了足够的语义线索，逐条写释义既费 token 又稀释注意力，
    所以第二层只给「拼写清单」，一行一个分类。
    """
    lines = []
    for c, items in grouped_names():
        lines.append(f"{CAT_TITLE.get(c, c)}：{'、'.join(_name_with_aka(t) for t in items)}")
    return lines


def export_md(path=None):
    """导出可读清单（给她在手机上翻的版本）。返回写出的 md 文本。

    **压缩版**：参考词表不再逐条开表格 + 释义，改成「分类：词1（别称）、词2…」一行一类，
    体积大概是原来的十分之一，长图也就短了（她 2026-09-20 要求）。
    """
    import datetime
    total = sum(len(v) for _, v in grouped_names())
    head = "# 口播稿专有名词库\n\n"
    head += (f"**更新**：{datetime.date.today().strftime('%Y-%m-%d')} ｜ "
             f"**纠错词条**：{len(TERMS)} 条 ｜ **参考词条**：{total} 条\n\n"
             "**怎么用**：转写时这些词会被优先识别；转写完**两层都会被自动改写**——"
             "第一层按登记的听错变体精确改回正确写法；第二层里的英文词条会自动归位"
             "（大小写、空格、拼错的字母），中文词条按变体表精确纠正。\n\n")
    body = "## 覆盖领域一览\n\n"
    for c, items in grouped_names():
        body += f"- {CAT_TITLE.get(c, c)}：{len(items)} 条\n"
    body += "\n## 一、纠错词表（听错会自动改）\n\n| 正确写法 | 常见听错 | 说明 |\n|---|---|---|\n"
    for t in TERMS:
        v = "、".join(t.get("variants", [])) or "—"
        body += f"| **{t['name']}** | {v} | {t['desc']} |\n"
    body += "\n## 二、参考词表（英文自动归位、中文按变体纠正）\n\n"
    for c, items in grouped_names():
        body += f"**{CAT_TITLE.get(c, c)}**（{len(items)}）：{'、'.join(_name_with_aka(t) for t in items)}\n\n"
    md = head + body
    if path:
        import os
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        open(path, "w", encoding="utf-8").write(md)
    return md


def suspects(text, limit=12):
    """巡检：捞出稿子里「像专有名词但不在词表」的英文词，交给人工确认。

    只用于打日志提醒，不改动正文（宁可漏报，不可乱改）。
    """
    known = {w.lower() for w in hotwords(budget=10 ** 6)}   # 巡检要全量，不受热词预算限制
    for t in TERMS + NAMES:
        known.add(t["name"].lower())
        for a in t.get("aka", []):
            known.add(a.lower())
    out, seen = [], set()
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9.\-]{1,20}", text or ""):
        w = m.group(0).strip(".-")
        low = w.lower()
        if low in known or w.upper() in STOPWORDS or len(w) < 2:
            continue
        if low in seen:
            continue
        seen.add(low)
        out.append(w)
        if len(out) >= limit:
            break
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        t, n = fix_text(" ".join(sys.argv[1:]))
        print(t)
        print(f"（纠正 {n} 处；可疑词：{suspects(t)}）")
    else:
        print(__doc__)
        print("热词：", "、".join(hotwords()))
        print("\n试一下：python3 fix_terms.py \"最近应该都被GV 刷屏了，把 Jev 接进 WorkerBody。\"")

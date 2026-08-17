"""
专家基座知识库 — 概念条目库
============================
由基座常量（TERM_ALIAS_MAP / DEEP_ANALYSIS / SCIENCE_POPULARIZATION /
CROSS_DOMAIN_KNOWLEDGE / WELDING_PROCESS_PARAMS / MATERIAL_PARAM_MAP）
+ 已学书籍章节 构建"概念 → 解析/应用/工艺类型/来源"条目。

每个概念条目：
- definition      概念解析（DEEP_ANALYSIS → 章节摘要 → 兜底）
- application     应用及拓展（CROSS_DOMAIN 实践指导 + 章节实践要点）
- process_types   支持的大体工艺类型（基座 6 工艺）
- process_params  工艺参数范围（若该概念是某工艺）
- material_params 材料参数（若该概念是某材料）
- sources         引用 PDF 上传书籍来源（章节标题 + 页码）

持久化到 saved_knowledge/expert_kb.json。
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("expert_kb")

# ============================================================
# 手工概念定义表（v2.6）
# 覆盖：书中未收录/词太专业/英文缩写/衍生概念。
# 构建时优先用手工定义，其次才从章节提取。
# 格式：{规范词: {"definition": ..., "application": ..., "process_types": [...]}}
# ============================================================
MANUAL_DEFINITIONS = {
    "瞬时液相": {
        "definition": "瞬时液相扩散焊（TLP bonding）：在两待焊表面之间放入熔点低于母材的中间层合金，加热至中间层熔化形成液相，液相传质充填间隙，随后在等温条件下液相向母材扩散、成分改变而使凝固点升高，最终等温凝固形成牢固接头。属于固相/液相扩散焊接（第9章范畴）。",
        "application": "用于高温合金、单晶叶片、陶瓷-金属等难焊材料的连接；相比普通扩散焊可降低温度/压力要求，接头组织均匀。航空发动机叶片、精密零部件修复常用。",
        "process_types": ["扩散焊接"],
    },
    "机器人焊接": {
        "definition": "机器人焊接：利用工业机器人（如 MR2010_1 六轴机器人）夹持焊枪，按预编程轨迹自动完成定位、送丝、焊接与姿态调整的自动化焊接方式。核心是焊缝跟踪、姿态规划（工作角/行走角）、焊接参数与机器人运动的协同控制。",
        "application": "适合大批量、重复性、多道多层或危险环境焊接；与工艺卡片结合可减少示教时间——输入材料/板厚/工艺即可获得电流电压、枪姿态、层道序列。",
        "process_types": ["GMAW/MIG (熔化极氩弧焊)", "FCAW (药芯焊丝CO₂焊)"],
    },
    "氢致开裂": {
        "definition": "氢致开裂（HIC/Hydrogen Induced Cracking）：焊接过程中溶解在金属中的氢在应力与显微缺陷处聚集，超过材料容纳能力后形成裂纹。是低合金高强钢、管线钢焊接冷裂纹的主要形式之一，与扩散氢含量、拘束度、冷却速度（t8/5）密切相关。",
        "application": "预防：焊材选用低氢焊条（E5015/E5016）、焊前预热、控制层间温度、后热消氢；厚板高强钢需严格控氢和冷却。",
        "process_types": [],
    },
    "超声探伤": {
        "definition": "超声探伤（UT）：利用超声波在工件内部传播时遇到缺陷界面产生反射回波，检测焊缝内部缺陷（气孔、夹渣、未熔合、裂纹）的无损检测方法。TOFD 是超声衍射时差法，利用缺陷端部衍射波精确定量缺陷尺寸。",
        "application": "厚板焊缝、压力容器、管道环缝的常规检测；TOFD/PAUT 用于要求更高的精确缺陷定量。检验要点：按 GB/T 11345 或 AWS D1.1 执行。",
        "process_types": [],
    },
    "临界区": {
        "definition": "临界区（ICHAZ/Intercritical HAZ）：焊接热影响区中被加热到 Ac1~Ac3 之间（临界温度区间）的窄带区域，该区发生不完全重结晶，原始组织部分奥氏体化、部分保留，晶粒不均匀，常是局部脆化区（LBZ）的组成部分。",
        "application": "对碳钢/低合金钢焊接接头韧性影响大；多层多道焊时临界区反复受热，需控制热输入与层间温度。",
        "process_types": [],
    },
    "活性钎料": {
        "definition": "活性钎料（如 Ag-Cu-Ti）：在传统钎料中加入活性元素 Ti 等，在钎焊过程中活性元素与陶瓷表面的氧化物反应，改善钎料对陶瓷的润湿性，从而实现陶瓷-金属、陶瓷-陶瓷的可靠连接（如 DBC 基板、SiC、AlN、Al2O3、ZrO2）。",
        "application": "用于功率电子封装（DBC 铜基板）、陶瓷与金属密封件、传感器等；活性钎焊温度通常 850-950°C，需真空或保护气氛。",
        "process_types": ["钎焊"],
    },
    "氩弧焊": {
        "definition": "氩弧焊是以氩气作为保护气体的电弧焊方法，分钨极氩弧焊（GTAW/TIG，电极不熔化）和熔化极氩弧焊（GMAW/MIG，焊丝熔化并填充）。氩气化学性质稳定，能有效隔离空气、防止焊缝氧化，故成形好、无飞溅，尤其适合有色金属、不锈钢及薄板焊接。",
        "application": "应用要点：氩弧焊（GTAW/TIG）用钨极+氩气保护，成形好、无飞溅，适合薄板/有色金属/根部打底焊；机器人焊接中常用于管道打底、不锈钢薄板；注意钨极烧损、控制气体流量10-15L/min。",
        "process_types": ["GTAW/TIG (钨极氩弧焊)", "GMAW/MIG (熔化极氩弧焊)"],
    },
    "埋弧焊": {
        "definition": "埋弧焊（SAW）：电弧在颗粒状焊剂层下燃烧，焊丝连续送进并熔化，熔渣覆盖熔池起到保护与保温作用。熔深大、熔敷效率高（95-99%）、无弧光辐射，适合中厚板长焊缝，广泛用于压力容器、船舶、管道纵缝。",
        "application": "应用要点：埋弧焊适合厚板/长直缝，自动化程度高，机器人常配变位机实现自动埋弧焊；注意焊剂干燥、焊丝对中、控制线能量。",
        "process_types": ["SAW (埋弧自动焊)"],
    },
    "熔滴过渡": {
        "definition": "熔滴过渡：焊条/焊丝端部熔化的金属以液滴形式脱离电极进入熔池的过程。主要形式：短路过渡（小电流，薄板/全位置）、颗粒状过渡（中电流）、射流过渡（大电流，熔滴细小高速，飞溅小）、射滴过渡。过渡形式决定电弧稳定性、飞溅与焊缝成形。",
        "application": "机器人焊接中通过电流电压/送丝匹配选择过渡形式：薄板用短路过渡，厚板/高速用射流过渡；脉冲 MIG 交替峰值电流稳定射滴过渡，减少飞溅。",
        "process_types": ["GMAW/MIG (熔化极氩弧焊)", "FCAW (药芯焊丝CO₂焊)"],
    },
    "熔池": {
        "definition": "熔池：焊接过程中母材与填充金属被电弧高温熔化形成的液态金属区域。熔池的尺寸、流动与凝固行为决定焊缝成形、气孔/夹渣倾向和结晶组织，受电流、电压、焊速、保护气等参数影响。",
        "application": "应用要点：熔池行为直接决定焊缝成形与缺陷（气孔/咬边）；机器人焊接中通过调整电流电压、焊速、送丝速度控制熔池尺寸与流动性，脉冲 MIG 可改善薄板熔池控制。",
        "process_types": [],
    },
    "焊条电弧焊": {
        "definition": "焊条电弧焊（SMAW/MMA）：利用焊条与工件之间产生的电弧热熔化焊条药皮与焊芯，药皮造渣/造气保护熔池，实现焊接的手工熔化焊方法。设备简单、适应性强，适合现场/小批量/全位置焊接，但效率较低。",
        "application": "应用要点：焊条按母材选（E4303 低碳钢、E5015/E5016 低合金钢），按板厚选直径（Φ2.5-Φ4.0）；机器人焊接一般用焊丝（GMAW）替代，SMAW 多用于补焊/现场。",
        "process_types": ["SMAW (焊条电弧焊)"],
    },
    "焊接热循环": {
        "definition": "焊接热循环：焊接过程中热影响区上某点经历的温度随时间变化的过程，用峰值温度 Tmax、高温停留时间 tH、冷却时间（t8/5 或 t100）表征。热循环决定 HAZ 的相变组织与硬度，是控制焊接冷裂纹和韧性关键。",
        "application": "应用要点：通过预热、控制线能量、后热调整热循环；高强钢控制 t8/5（如 5-20s）避免马氏体脆化；机器人焊接可精确控制焊速稳定热循环。",
        "process_types": [],
    },
    "冷裂纹": {
        "definition": "冷裂纹：焊接接头冷却到较低温度（<200°C）后产生的裂纹，发生在热影响区或焊缝，由淬硬组织+扩散氢+拘束应力三要素共同作用引起。是高强钢、中碳钢焊接的主要缺陷，又称延迟裂纹。",
        "application": "应用要点：预防靠控制三要素：低氢焊材（E5015/E5016）、预热/层间温度、后热消氢；按碳当量 Ceq/Pcm 评估敏感性；厚板高强钢严格控氢控冷。",
        "process_types": [],
    },
    "不锈钢": {
        "definition": "不锈钢：含铬量≥10.5% 的耐蚀钢，靠表面致密 Cr2O3 钝化膜抗腐蚀。分奥氏体（304/316）、铁素体、马氏体、双相不锈钢。焊接难点：晶间腐蚀（敏化区 Cr 贫化）、热裂纹、变形控制。",
        "application": "应用要点：奥氏体不锈钢焊材选 ER308L/ER316L，控层间温度≤150°C 防敏化；薄板变形大需刚性固定；机器人 MIG 焊不锈钢用 98%Ar+2%CO₂ 保护。",
        "process_types": ["GMAW/MIG (熔化极氩弧焊)", "GTAW/TIG (钨极氩弧焊)"],
    },
    "铝合金": {
        "definition": "铝合金：以铝为基的轻合金，密度小、导电导热好、耐蚀。焊接难点：表面致密 Al2O3 氧化膜熔点高（2050°C）阻碍熔合、易生气孔（氢溶度突变）、热裂纹、热变形大。",
        "application": "应用要点：焊前彻底清理氧化膜（钢丝刷/化学清理），用 ER5356（5系）/ER4043（6系）焊丝；机器人 MIG 焊用纯 Ar 脉冲，大电流高速焊，控层间温度≤100°C。",
        "process_types": ["GMAW/MIG (熔化极氩弧焊)", "GTAW/TIG (钨极氩弧焊)"],
    },
    "扩散连接": {
        "definition": "扩散连接（扩散焊）：在温度和压力作用下，两清洁金属表面原子相互扩散形成牢固接头的固态连接方法。需真空/保护气氛，温度约 0.5-0.8 熔点；TLP 扩散焊用中间层液相降低要求。用于高温合金、钛合金、陶瓷等难熔材料。",
        "application": "应用要点：控制温度/压力/时间/表面粗糙度；机器人扩散焊多用于精密零件与异种材料；接头组织均匀、变形小，适合单晶叶片、蜂窝结构。",
        "process_types": [],
    },
    "应力腐蚀开裂": {
        "definition": "应力腐蚀开裂（SCC）：金属在腐蚀介质和拉应力共同作用下产生的脆性开裂，裂纹沿晶界或穿晶扩展。常见于奥氏体不锈钢（Cl⁻介质）、高强钢（湿 H2S）等，是承压设备主要失效形式。",
        "application": "应用要点：预防靠控制应力（消除残余应力）+ 选材（双相不锈钢替代奥氏体）+ 控介质；焊接残余拉应力是重要诱因，焊后热处理/喷丸消除。",
        "process_types": [],
    },
    # === 常用工艺方法 ===
    "激光焊": {
        "definition": "激光焊（LBW）：利用高能量密度激光束（CO₂/光纤/YAG）熔化金属实现焊接。能量密度极高、热影响区小、变形小、深宽比大，适合精密件、高速焊、难熔材料，可实现非接触/远距离焊接。",
        "application": "应用要点：机器人激光焊需激光视觉焊缝跟踪；薄板高速拼焊、电池极耳、精密薄壁件；光斑对中要求高，注意保护镜片与安全防护。",
        "process_types": [],
    },
    "电子束焊": {
        "definition": "电子束焊（EBW）：在真空室中利用高速电子束轰击工件，动能转化为热能使金属熔化焊接。能量密度极高、深宽比大（可达30:1）、热影响区极小、无氧化，适合厚件、难熔金属、精密件。",
        "application": "应用要点：需真空室、设备昂贵；机器人多用于焊缝自动对中与装夹；常用于航空发动机、钛合金、厚壁压力容器。",
        "process_types": [],
    },
    "摩擦焊": {
        "definition": "摩擦焊（FW）：利用工件接触面高速相对旋转摩擦生热使材料塑化，加压顶锻形成固态连接。属固态焊接，热影响区小、无熔化缺陷、接头强度高，适合轴类、管类、异种金属。",
        "application": "应用要点：机器人摩擦焊适合大批量轴件/管件；搅拌摩擦焊（FSW）用于铝/镁合金板拼焊，无熔池、变形极小。",
        "process_types": [],
    },
    "药芯焊丝电弧焊": {
        "definition": "药芯焊丝电弧焊（FCAW）：使用内部装填药粉的管状焊丝，电弧熔化焊丝时药芯造渣/造气保护熔池。兼有焊条的药皮保护和焊丝的连续送丝高效率，适合户外、大电流、全位置。",
        "application": "应用要点：机器人 FCAW 效率高，用于中厚板/结构件；注意清渣与烟尘，选低飞溅药芯焊丝；电流 100-500A。",
        "process_types": ["FCAW (药芯焊丝CO₂焊)"],
    },
    "堆焊": {
        "definition": "堆焊：在工件表面熔敷一层金属以恢复尺寸或获得耐磨/耐蚀/耐热表面层的方法。用于磨损件修复、耐磨层（D256/D322/D707）、耐蚀衬层。",
        "application": "应用要点：机器人堆焊控稀释率与层间温度；多层多道逐层堆焊，注意与母材的相容性。",
        "process_types": ["GMAW/MIG (熔化极氩弧焊)", "FCAW (药芯焊丝CO₂焊)"],
    },
    "气焊": {
        "definition": "气焊：利用氧-乙炔（或其他燃气）火焰作为热源熔化母材和焊丝进行焊接的方法。设备简单、不需电源，但热输入大、变形大、效率低，主要用于薄板、有色金属小件、补焊。",
        "application": "应用要点：机器人焊接基本不用气焊；仅薄板/小件/现场补焊场景；注意氧-乙炔安全。",
        "process_types": [],
    },
    "激光熔覆": {
        "definition": "激光熔覆：用激光束将合金粉末/丝材熔覆在工件表面形成冶金结合层，用于表面强化与修复。稀释率低、热影响区小、结合强度高，可精确控制覆层厚度。",
        "application": "应用要点：机器人激光熔覆用于模具/叶片/轴类修复与耐磨耐蚀涂层；粉末配比与送粉速度控制关键。",
        "process_types": [],
    },
    "热喷涂": {
        "definition": "热喷涂：将涂层材料（粉末/丝材）加热至熔融/半熔融状态，高速喷射到工件表面形成涂层的表面技术。分火焰/等离子/HVOF（超音速火焰）等，用于耐磨耐蚀耐热涂层。",
        "application": "应用要点：HVOF 涂层致密结合强度高（碳化钨耐磨层）；机器人自动喷涂均匀控制厚度；工件表面预处理（喷砂）关键。",
        "process_types": [],
    },
    # === 材料 ===
    "高强钢": {
        "definition": "高强钢：屈服强度≥460MPa 的低合金高强度钢（Q460/Q690 等），通过控轧控冷+微合金化获得高强度。焊接性随强度升高变差：碳当量高、冷裂倾向大、HAZ 硬度升高。",
        "application": "应用要点：严格控预热/层间温度/后热，控热输入限制 t8/5；用低氢焊材 E5515/E6015；机器人焊接精确控线能量保证韧性。",
        "process_types": ["GMAW/MIG (熔化极氩弧焊)", "FCAW (药芯焊丝CO₂焊)"],
    },
    "低合金钢": {
        "definition": "低合金钢：含合金元素总量<5% 的结构钢（Q345/16Mn 等），通过 Mn/Si/微合金强化提高强度与韧性。焊接性良好，厚板/高拘束时需防冷裂。",
        "application": "应用要点：Q345 一般不需预热（>38mm 才需 100-150°C）；焊材 E5015/E5016 或 ER50-6 焊丝；机器人焊接适用于中厚板结构件。",
        "process_types": ["GMAW/MIG (熔化极氩弧焊)", "FCAW (药芯焊丝CO₂焊)"],
    },
    "镍基合金": {
        "definition": "镍基合金（Inconel 600/625/718 等）：以镍为主的耐高温/耐蚀合金，强度高、抗氧化、抗热腐蚀。焊接难点：热裂纹敏感、熔池粘度大、易生气孔。",
        "application": "应用要点：用 ERNiCr-3 等焊材，控热输入防热裂，层间温度≤150°C；机器人焊用于高温/耐蚀部件；氩气保护流量充足。",
        "process_types": ["GTAW/TIG (钨极氩弧焊)", "GMAW/MIG (熔化极氩弧焊)"],
    },
    "钛合金": {
        "definition": "钛合金：以钛为基的轻高强合金，比强度高、耐蚀性好，分 α/α+β/β 三类（TA/TC/TB 牌号）。焊接难点：高温下极易吸氧/氢/氮脆化，需严格惰性气体保护。",
        "application": "应用要点：必须纯氩保护（焊缝/背面/热影响区三区），控制热输入防 β 相脆化；机器人 TIG 焊钛合金用于航空结构件。",
        "process_types": ["GTAW/TIG (钨极氩弧焊)"],
    },
    "铸铁": {
        "definition": "铸铁：含碳量>2.11% 的铁碳合金（灰铁 HT200、球铁 QT450 等）。焊接难点：白口化、热裂纹、冷裂纹严重，焊接性极差，多用于修复而非新焊。",
        "application": "应用要点：热焊预热 600-700°C 用 Z408 镍铁焊条；冷焊用 Z308 纯镍焊条小电流短道焊；机器人铸铁焊多为补焊/堆焊修复。",
        "process_types": [],
    },
    # === 缺陷 ===
    "热裂纹": {
        "definition": "热裂纹：焊接过程中在高温下（凝固/液化）产生的裂纹，发生在焊缝（结晶裂纹）或近缝区（液化裂纹），由低熔点共晶/偏析 + 收缩应力引起。奥氏体钢、铝合金敏感。",
        "application": "应用要点：控制焊缝成分（低 S/P、加细化元素）、减小拘束、控制热输入与焊道形状；用低偏析焊材。",
        "process_types": [],
    },
    "气孔": {
        "definition": "气孔：焊接熔池凝固前气体（氢/氮/CO）未逸出形成的空穴。氢气孔主要来自油污/水/焊材吸潮；CO 气孔来自碳氧化。是焊缝常见缺陷，降低致密性与强度。",
        "application": "应用要点：清理焊件（油/锈/水）、焊材烘干、保护气流量充足（15-25L/min）防风、短弧操作。",
        "process_types": [],
    },
    "未熔合": {
        "definition": "未熔合：焊缝金属与母材或相邻焊道间未充分熔化结合的缺陷，降低承载能力，是重要的返修原因。由热输入不足、坡口不洁、焊枪角度不当引起。",
        "application": "应用要点：适当增大电流、降低焊速、保证熔池边缘充分熔化；多层焊清理层间；机器人焊控制枪角度与摆动。",
        "process_types": [],
    },
    "咬边": {
        "definition": "咬边：沿焊缝边缘母材被熔化形成低于母材表面的凹槽缺陷，削弱接头截面、产生应力集中。由电流过大、焊速过快、焊枪角度不当引起。",
        "application": "应用要点：减小电流、控制焊枪角度、适当摆动；机器人焊调整摆动宽度与停留时间消除咬边。",
        "process_types": [],
    },
    "变形": {
        "definition": "焊接变形：焊接不均匀加热冷却引起的残余应力导致的工件尺寸/形状变化（角变形/收缩/弯曲/扭曲）。由热输入、拘束度、焊接顺序决定。",
        "application": "应用要点：对称焊/分段退焊/反变形法；机器人焊通过对称排道、控制线能量减小变形；焊后校形。",
        "process_types": [],
    },
    # === 检测/组织 ===
    "无损检测": {
        "definition": "无损检测（NDT）：不破坏工件而检测内部/表面缺陷的方法。射线 RT（内部气孔/夹渣）、超声 UT（内部裂纹）、磁粉 MT/渗透 PT（表面裂纹）、相控阵 PAUT、TOFD。",
        "application": "应用要点：压力容器/承重焊缝按标准抽检（GB/T 11345、AWS D1.1）；厚板用 UT/RT，表面用 MT/PT；机器人焊后自动检测。",
        "process_types": [],
    },
    "断裂韧性": {
        "definition": "断裂韧性：材料抵抗裂纹扩展的能力，用 KIC（平面应变断裂韧度）、CTOD、J 积分表征。高断裂韧性=允许更大临界裂纹尺寸，是承压/低温结构选材与验收的关键指标。",
        "application": "应用要点：低温/承压工况要求 CTOD/J 达标；焊接接头韧性受 HAZ 组织/杂质影响，控热输入+选高韧性焊材。",
        "process_types": [],
    },
    "贝氏体": {
        "definition": "贝氏体：钢在珠光体转变与马氏体转变之间的中温（约 400-550°C）转变产物，分上贝氏体/下贝氏体。HAZ 中的贝氏体组织影响硬度与韧性，下贝氏体韧性较好。",
        "application": "应用要点：通过控制 t8/5 冷却速度控制贝氏体形态；多层多道焊改善组织；高强钢 HAZ 贝氏体硬化需预热控制。",
        "process_types": [],
    },
    "再结晶": {
        "definition": "再结晶：冷变形金属在加热到一定温度后，形成新的等轴晶粒、消除加工硬化的过程。多层多道焊热循环使 HAZ 再结晶，可细化晶粒改善韧性。",
        "application": "应用要点：控制热输入避免再结晶后的晶粒长大；多层焊利用再结晶改善组织。",
        "process_types": [],
    },
    "相变": {
        "definition": "相变：材料在温度变化时发生的组织转变（如奥氏体→铁素体/珠光体/贝氏体/马氏体）。焊接中母材与焊缝经历的固态相变决定最终组织与性能。",
        "application": "应用要点：通过冷却速度（t8/5）控制相变产物；CCT 曲线指导焊接热循环设计；高强钢控相变防冷裂。",
        "process_types": [],
    },
    "韧性": {
        "definition": "韧性：材料抵抗断裂、吸收变形能量的能力，用冲击功（KV）、断裂韧性（KIC/CTOD/J）表征。低韧性材料易脆断，焊接接头的韧性受组织、杂质、残余应力影响。",
        "application": "应用要点：低温/承压工况关注 DBTT；控热输入细化组织、选纯净焊材提升韧性；机器人焊保证接头韧性。",
        "process_types": [],
    },
    "碳扩散": {
        "definition": "碳扩散：异种钢焊接（如珠光体钢+奥氏体不锈钢）中，碳从低合金侧向高合金侧扩散迁移，形成母材侧脱碳层与焊缝侧增碳层，降低高温性能并产生应力集中。",
        "application": "应用要点：用镍基中间层阻止碳迁移；控制焊后热处理温度/时间；异种钢接头设计考虑碳扩散影响。",
        "process_types": [],
    },
    # === 组织/结晶 ===
    "铁素体": {
        "definition": "铁素体：碳溶解于 α-Fe（体心立方）中形成的固溶体组织，强度硬度低、塑韧性好。焊缝/HAZ 中铁素体比例与形态决定韧性，针状铁素体韧性最佳。",
        "application": "应用要点：低合金钢焊缝希望获得针状铁素体（韧性好）；控冷却速度与微合金化（Ti/B）促进。",
        "process_types": [],
    },
    "奥氏体": {
        "definition": "奥氏体：碳溶解于 γ-Fe（面心立方）中形成的固溶体组织，高温存在，室温存在于奥氏体不锈钢。奥氏体钢无磁性、韧性好，但热裂敏感。",
        "application": "应用要点：奥氏体不锈钢焊接控热输入防晶间腐蚀与热裂；奥氏体-铁素体平衡用 WRC 图/Schaeffler 图。",
        "process_types": [],
    },
    "马氏体": {
        "definition": "马氏体：奥氏体快速冷却（过冷到 Ms 以下）形成的体心正方/体心立方过饱和固溶体，硬度高、脆性大。焊接中 HAZ 形成马氏体导致硬化与冷裂倾向。",
        "application": "应用要点：高强钢控冷却（预热/后热）避免 HAZ 全马氏体；用 Ceq/Pcm 评估硬化倾向。",
        "process_types": [],
    },
    "珠光体": {
        "definition": "珠光体：铁素体与渗碳体层片交替的机械混合物，由奥氏体共析转变形成。强度硬度适中，用于普通结构钢。",
        "application": "应用要点：普通碳钢焊接 HAZ 珠光体转变正常；厚板/高碳钢注意冷裂。",
        "process_types": [],
    },
    "枝晶": {
        "definition": "枝晶：金属凝固时晶体沿优先生长方向（热流反方向）长出树枝状晶体的形态。焊缝凝固为柱状枝晶，枝晶间偏析影响裂纹倾向。",
        "application": "应用要点：控冷却与变质处理细化枝晶；低 S/P 减少枝晶间偏析防热裂。",
        "process_types": [],
    },
    "柱状晶": {
        "definition": "柱状晶：焊缝金属凝固时沿垂直于熔池边界的方向（散热方向）生长的长条状晶粒。柱状晶粗大、方向性强，降低韧性。",
        "application": "应用要点：多层多道焊打断柱状晶；控热输入/加变质剂细化。",
        "process_types": [],
    },
    "等轴晶": {
        "definition": "等轴晶：各方向尺寸相近的等大晶粒，是凝固后期的中心等轴区或再结晶产物。等轴晶组织均匀、韧性好。",
        "application": "应用要点：变质处理/电磁搅拌促进等轴晶；多层焊改善组织。",
        "process_types": [],
    },
    "偏析": {
        "definition": "偏析：合金元素/杂质在凝固过程中不均匀分布的现象（枝晶偏析/区域偏析）。偏析导致成分不均、降低性能、促发裂纹。",
        "application": "应用要点：低 S/P 焊材、控冷却速度、焊后扩散退火减轻偏析。",
        "process_types": [],
    },
    "晶粒": {
        "definition": "晶粒：多晶材料中单晶体的小单元，晶粒大小（晶粒度）影响强度与韧性（细晶强化、细晶增韧）。焊接热循环影响 HAZ 晶粒长大。",
        "application": "应用要点：控热输入避免 HAZ 晶粒粗化；多层多道焊细化晶粒。",
        "process_types": [],
    },
    "晶粒长大": {
        "definition": "晶粒长大：加热过程中晶粒合并长大的现象，发生在再结晶之上温度。HAZ 靠近熔合线处晶粒粗化，降低韧性。",
        "application": "应用要点：控峰值温度与高温停留时间；高强钢避免 HAZ 粗晶脆化。",
        "process_types": [],
    },
    "形核": {
        "definition": "形核：凝固/相变初始阶段形成新相核心的过程（均匀形核/非均匀形核）。焊缝凝固以非均匀形核为主（熔池壁/杂质）。",
        "application": "应用要点：变质剂（Ti/B/RE）促进非均匀形核细化晶粒；控过冷度。",
        "process_types": [],
    },
    # === 坡口/焊材 ===
    "V形坡口": {
        "definition": "V形坡口：对接接头的一种坡口形式，两母材端面加工成 V 形，坡口角约 60°、钝边 1-2mm。用于中厚板单面/双面焊，需保证根部熔透。",
        "application": "应用要点：V形坡口填充量适中，机器人焊按坡口深度规划层道；钝边+间隙保证打底熔透。",
        "process_types": [],
    },
    "X形坡口": {
        "definition": "X形坡口：厚板对接的双面 V 形坡口，两面各开 V 形，减少单面填充量、平衡两面变形。用于厚板双面焊。",
        "application": "应用要点：先焊一面清根再焊另一面；对称焊接控制变形；机器人焊排道对称。",
        "process_types": [],
    },
    "焊条直径": {
        "definition": "焊条直径：焊条规格参数（Φ2.0-Φ6.0mm），决定电流范围与熔敷效率。按板厚选：Φ2.5 焊薄板、Φ3.2 最常用（3-12mm）、Φ4.0/Φ5.0 焊中厚板。",
        "application": "应用要点：直径大→电流大、熔敷快；薄板用细焊条防烧穿；机器人焊多用焊丝（1.0-1.2mm）。",
        "process_types": [],
    },
    "焊丝": {
        "definition": "焊丝：连续送进的填充金属丝，用于熔化极气体保护焊（GMAW/FCAW）等。材质与母材匹配（ER50-6 低碳钢、ER308L 不锈钢、ER5356 铝）。",
        "application": "应用要点：焊丝直径 0.8-1.6mm，直径/送丝速度决定电流；机器人焊用盘装焊丝连续送丝。",
        "process_types": ["GMAW/MIG (熔化极氩弧焊)"],
    },
    "线能量": {
        "definition": "线能量（热输入）：单位长度焊缝获得的热量 = 电流×电压/焊速（kJ/mm）。决定热影响区组织与冷却速度，是控制 HAZ 性能与变形的关键参数。",
        "application": "应用要点：高强钢限制线能量上限防 HAZ 脆化；薄板控下限防烧穿；机器人焊调焊速精确控制。",
        "process_types": [],
    },
    # === 材料 ===
    "低碳钢": {
        "definition": "低碳钢：含碳量≤0.25% 的碳素钢（Q235/Q255、10/20 号钢），焊接性优良，一般不需预热，是最易焊的钢种。",
        "application": "应用要点：焊材 E4303/E5015 或 ER50-6 焊丝；厚板/高拘束才需预热；机器人 GMAW 焊接常规结构件。",
        "process_types": ["GMAW/MIG (熔化极氩弧焊)", "SMAW (焊条电弧焊)"],
    },
    "铜合金": {
        "definition": "铜合金：以铜为基的合金（黄铜 H62/H68、青铜 QAl9-4 等），导电导热好、耐蚀。焊接难点：导热快需大热输入、易氧化、热裂、气孔。",
        "application": "应用要点：预热、用 TIG/MIG 大电流，脱氧铜焊丝；机器人焊铜合金需大功率电源。",
        "process_types": ["GTAW/TIG (钨极氩弧焊)"],
    },
    # === 检测/缺陷 ===
    "未焊透": {
        "definition": "未焊透：焊缝根部/坡口根部未被完全熔透的缺陷，减薄有效截面、降低强度。由坡口角小/间隙小/电流不足/焊速过快引起。",
        "application": "应用要点：保证打底电流充足、坡口角与间隙合适、背面清根；机器人焊控制根部熔透。",
        "process_types": [],
    },
    "夹杂": {
        "definition": "夹杂：焊缝中的非金属夹杂物（夹渣、夹钨、氧化物），降低韧性、引起应力集中。由清渣不净、运条不当、焊材杂质引起。",
        "application": "应用要点：多层焊每层清渣、正确运条、选纯净焊材。",
        "process_types": [],
    },
    "氢脆": {
        "definition": "氢脆：扩散氢在应力作用下在金属中聚集导致塑性/韧性下降的现象。高强钢敏感，与冷裂纹密切相关。",
        "application": "应用要点：低氢焊材、焊前预热、焊后消氢（250°C×2h）；控制环境湿度。",
        "process_types": [],
    },
    "热等静压": {
        "definition": "热等静压（HIP）：在高温高压（等静压气体）下处理工件，消除内部孔隙/闭合缺陷，使组织致密化。用于铸件、增材件、扩散焊件。",
        "application": "应用要点：焊接关键承力件可 HIP 消除微小缺陷；设备昂贵，用于高端件。",
        "process_types": [],
    },
    "相控阵": {
        "definition": "相控阵超声检测（PAUT）：由多个压电晶片组成阵列探头，通过相位控制电子偏转/聚焦声束，实现对焊缝的快速/精确扫查成像。",
        "application": "应用要点：比常规 UT 效率高、成像直观；用于管道环缝、厚板焊缝检测。",
        "process_types": [],
    },
    # === 冶金 ===
    "冶金": {
        "definition": "冶金：金属的冶炼与合金化过程。焊接冶金指熔池中的熔炼反应（氧化/还原/脱氧/脱硫/合金化），决定焊缝化学成分与性能。",
        "application": "应用要点：焊材的冶金反应补偿烧损（脱氧剂 Mn/Si）；控杂质元素保证焊缝纯净度。",
        "process_types": [],
    },
    "脱氧": {
        "definition": "脱氧：焊接冶金中去除熔池内氧的反应，用脱氧元素（Mn、Si、Ti、Al）与氧结合生成氧化物进入渣中，防止焊缝气孔与氧化夹杂。",
        "application": "应用要点：焊丝含脱氧剂（ER50-6 含 Mn/Si）；气体保护+脱氧双保险防气孔。",
        "process_types": [],
    },
    "脱硫": {
        "definition": "脱硫：去除金属中硫的反应，硫易形成低熔点共晶导致热裂。焊接冶金中通过渣系脱硫，提高焊缝抗热裂性。",
        "application": "应用要点：碱性焊条/药芯焊丝脱硫能力强；低 S 母材/焊材防热裂。",
        "process_types": [],
    },
    "脱磷": {
        "definition": "脱磷：去除金属中磷的反应，磷增加冷脆性（降低低温韧性）。焊接冶金中通过氧化性渣脱磷。",
        "application": "应用要点：低 P 焊材保证低温韧性；控磷含量。",
        "process_types": [],
    },
    "氧化": {
        "definition": "氧化：金属与氧反应生成氧化物。焊接中熔池氧化导致合金烧损、气孔、夹杂；保护气体/焊剂/药皮起防氧化作用。",
        "application": "应用要点：气体保护充分、焊剂/药皮覆盖；钛/铝等活性金属需强保护。",
        "process_types": [],
    },
    "还原": {
        "definition": "还原：从金属氧化物中夺取氧使金属还原析出的反应。焊接冶金中通过还原性气氛/元素控制焊缝成分。",
        "application": "应用要点：焊剂中还原剂控制焊缝氧含量。",
        "process_types": [],
    },
    "合金化": {
        "definition": "合金化：通过焊材向焊缝过渡合金元素（Mn/Si/Cr/Ni/Mo），调整焊缝成分与性能（强度、韧性、耐蚀）。",
        "application": "应用要点：按母材/焊缝性能要求选焊材合金体系；机器人焊匹配焊丝合金。",
        "process_types": [],
    },
    "焊接性": {
        "definition": "焊接性：材料在规定的焊接工艺下获得无缺陷且满足使用性能接头的能力。用碳当量 Ceq、冷裂敏感指数 Pcm 等评估，分工艺焊接性与使用焊接性。",
        "application": "应用要点：高碳当量钢需预热/低氢焊材；焊接性差的材料选特殊工艺（预热/后热/低热输入）。",
        "process_types": [],
    },
    "后热": {
        "definition": "后热：焊后立即对焊缝区加热保温，减缓冷却速度、促进氢逸出，防止冷裂纹。常与预热/层间温度配合。",
        "application": "应用要点：高强钢焊后立即后热（如 200-250°C×2h）；厚板受压件按规范。",
        "process_types": [],
    },
    "板厚": {
        "definition": "板厚：母材厚度，决定坡口形式、预热温度、层道数、焊接电流范围。是工艺卡片的关键输入参数。",
        "application": "应用要点：板厚→坡口（≤6不开/12单V/25X）→层道（2层/3-4层/多层）→电流；机器人焊按板厚排道。",
        "process_types": [],
    },
    "强度": {
        "definition": "强度：材料抵抗变形与断裂的能力，用屈服强度/抗拉强度表征。焊接接头强度通常需不低于母材，由焊缝金属成分与组织决定。",
        "application": "应用要点：焊缝强度匹配原则：等强匹配/低强匹配；控 HAZ 硬化（过高=脆化，过低=弱化）。",
        "process_types": [],
    },
    "断裂": {
        "definition": "断裂：材料在载荷作用下破坏分离的过程，分脆性断裂（无明显变形）与韧性断裂（伴随塑性变形）。焊接接头断裂多始于缺陷/应力集中处。",
        "application": "应用要点：控缺陷、应力集中、韧性；低温/承压工况防脆断。",
        "process_types": [],
    },
    "脆性断裂": {
        "definition": "脆性断裂：无明显塑性变形即发生的快速断裂，断口平齐、危害大。由低温（低于 DBTT）、应力集中、材料脆化引起。",
        "application": "应用要点：保证低温韧性（控组织/杂质）；消除应力集中；压力容器/桥梁重点防控。",
        "process_types": [],
    },
    # === 剩余高频 ===
    "裂纹": {
        "definition": "裂纹：焊缝/HAZ 中局部断裂形成的缝隙缺陷，分热裂纹（高温凝固/液化）、冷裂纹（低温氢致）、再热裂纹。是焊接最危险的缺陷，降低强度与安全。",
        "application": "应用要点：按裂纹类型防控：热裂控成分/拘束，冷裂控氢/预热/层间温度，再热裂控 PWHT 参数；无损检测排查。",
        "process_types": [],
    },
    "韧脆转变温度": {
        "definition": "韧脆转变温度（DBTT/FATT）：材料韧性随温度降低由韧性断裂转为脆性断裂的临界温度。低于 DBTT 材料变脆，是低温结构选材/验收关键指标。",
        "application": "应用要点：低温/承压工况要求 DBTT 低于工作温度；焊接接头通过控组织/杂质提升低温韧性。",
        "process_types": [],
    },
    "韧性断裂": {
        "definition": "韧性断裂（延性断裂）：伴随明显塑性变形、吸收大量能量的断裂，断口呈纤维状。比脆性断裂安全，是合格结构的理想失效模式。",
        "application": "应用要点：通过细化组织、减少夹杂提升抗韧性断裂能力；韧性断裂前有明显变形便于预警。",
        "process_types": [],
    },
    "细晶区": {
        "definition": "细晶区（FGHAZ）：HAZ 中加热到 Ac3 以上但晶粒未明显长大的区域，晶粒细小、韧性好，是 HAZ 中性能较优的区域。",
        "application": "应用要点：多层多道焊增加细晶区比例改善接头韧性。",
        "process_types": [],
    },
    "粗晶区": {
        "definition": "粗晶区（CGHAZ）：HAZ 中靠近熔合线、加热到高温使晶粒显著粗化的区域，韧性差，是 HAZ 性能薄弱区，可能含局部脆化区（LBZ）。",
        "application": "应用要点：控热输入/峰值温度避免粗化；多层多道焊细化粗晶区。",
        "process_types": [],
    },
    "冷裂纹敏感性": {
        "definition": "冷裂纹敏感性：材料对焊接冷裂纹的敏感程度，用碳当量 Ceq、冷裂敏感指数 Pcm 评估。值越高，需预热/低氢焊材/后热的程度越大。",
        "application": "应用要点：Ceq>0.4 需预热，>0.5 严格控氢+后热；插销试验/Tekken 试验定量测定。",
        "process_types": [],
    },
    "局部脆化区": {
        "definition": "局部脆化区（LBZ）：HAZ 粗晶区/临界区中的韧性薄弱微区，是压力容器用钢接头断裂的起点。由组织粗化/马氏体岛（M-A）等引起。",
        "application": "应用要点：控热输入、多层多道细化组织、选高韧性钢种；CTOD 试验评估。",
        "process_types": [],
    },
    "脱碳层": {
        "definition": "脱碳层：异种钢/高温服役中碳从母材侧向焊缝侧扩散后，母材近缝区含碳量下降形成的软层，强度下降。",
        "application": "应用要点：用镍基中间层阻止碳迁移；控制焊后热处理。",
        "process_types": [],
    },
    "增碳层": {
        "definition": "增碳层：碳扩散到焊缝/高合金侧形成的富碳层，硬度升高、易脆化，降低接头高温性能。",
        "application": "应用要点：镍基中间层+控热处理温度抑制增碳。",
        "process_types": [],
    },
    "活性钎焊": {
        "definition": "活性钎焊：用含活性元素（Ti 等）的钎料（Ag-Cu-Ti）钎焊陶瓷与金属，活性元素与陶瓷表面反应改善润湿性，实现陶瓷连接。",
        "application": "应用要点：用于 DBC 基板、SiC/AlN/Al2O3 陶瓷-金属封装；真空钎焊 850-950°C。",
        "process_types": [],
    },
    "表面改性": {
        "definition": "表面改性：通过改变材料表面成分/组织/形貌获得耐磨、耐蚀、耐热等性能的表面工程技术（激光熔覆、热喷涂、渗碳渗氮、Mo-Mn 金属化等）。",
        "application": "应用要点：机器人表面改性用于模具/叶片/轴类强化修复；工艺参数控涂层结合强度。",
        "process_types": [],
    },
    "反应层": {
        "definition": "反应层：异种材料连接/钎焊时界面处元素相互扩散反应形成的化合物层（如 Fe2Al5、TiAl），过厚则脆化。",
        "application": "应用要点：控制反应层厚度（温度/时间）；中间层抑制脆性相。",
        "process_types": [],
    },
    "润湿": {
        "definition": "润湿：液体（钎料/焊料）在固体表面铺展的能力，用润湿角表征。润湿性好→填充与结合好，是钎焊/焊接质量前提。",
        "application": "应用要点：清理表面+助焊剂/活性元素改善润湿；机器人钎焊控制温度与表面处理。",
        "process_types": [],
    },
    "界面反应": {
        "definition": "界面反应：异种材料连接界面处发生的化学/扩散反应（金属间化合物形成、元素迁移），决定接头结合强度。",
        "application": "应用要点：控反应温度/时间/中间层；钛-钢、铝-钢等异种连接重点控制。",
        "process_types": [],
    },
    "金属间化合物": {
        "definition": "金属间化合物：两种金属按一定比例形成的化合物相（如 Fe2Al5、TiAl、Ni3Al），硬度高、脆性大。异种金属焊接界面易形成，厚则脆断。",
        "application": "应用要点：用中间层/控热输入抑制其生长；机器人异种焊需控界面厚度。",
        "process_types": [],
    },
    "MIG焊": {
        "definition": "MIG 焊（熔化极惰性气体保护焊）：用惰性气体（Ar/He）保护的熔化极电弧焊，焊丝连续送进熔化填充。适合铝/铜/不锈钢等，飞溅小、成形好。",
        "application": "应用要点：MIG 用于有色金属/不锈钢；MAG 用 CO₂/Ar+CO₂ 用于碳钢；机器人 MIG 焊精确控参数。",
        "process_types": ["GMAW/MIG (熔化极氩弧焊)"],
    },
    "等离子焊": {
        "definition": "等离子弧焊（PAW）：通过喷嘴压缩电弧形成高温高能量密度等离子弧焊接。能量集中、熔深大，可实现穿孔焊，适合不锈钢薄-中板、精密件。",
        "application": "应用要点：微束等离子焊薄板/精密件；穿孔焊厚板单面焊双面成形；机器人 PAW 控离子气流量。",
        "process_types": ["PAW (等离子弧焊)"],
    },
    "活性金属": {
        "definition": "活性金属：化学活泼性强、易与氧/氮/氢反应的金属（钛、锆、铝等）。焊接时需强保护（惰性气体/真空）防止氧化氮化。",
        "application": "应用要点：钛/锆焊必须纯 Ar 三区保护；铝需清氧化膜；机器人焊活性金属控气氛。",
        "process_types": [],
    },
    "焊后热处理": {
        "definition": "焊后热处理（PWHT）：焊后对工件加热保温的工艺，目的消除残余应力、改善组织/硬度、脱氢。分消除应力退火、回火、正火等。",
        "application": "应用要点：受压件厚板按规范 PWHT（如 600-650°C 去应力）；高强钢控温度防回火脆化。",
        "process_types": [],
    },
    "层间温度": {
        "definition": "层间温度：多层多道焊时，下一道开始前上一道已冷却到的温度。控制层间温度可防止过热、控制 HAZ 组织。",
        "application": "应用要点：一般≤200-250°C；不锈钢防敏化≤150°C；高强钢按规范；机器人焊连续排道控温。",
        "process_types": [],
    },
    "异种钢": {
        "definition": "异种钢：化学成分/组织不同的两种钢（如珠光体钢+奥氏体钢、碳钢+不锈钢）的焊接。难点：碳迁移、热膨胀差、成分稀释、组织不均。",
        "application": "应用要点：选匹配焊材（高合金侧）、镍基中间层防碳迁移、控热处理；机器人异种焊按接头设计工艺。",
        "process_types": [],
    },
    "金相组织": {
        "definition": "金相组织：金属的显微组织（铁素体/珠光体/贝氏体/马氏体/M-A 等），用金相显微镜/扫描电镜观察，评估焊接接头组织与性能关系。",
        "application": "应用要点：金相检验评估焊缝/HAZ 组织；SEM/TEM/EBSD 分析微观缺陷与析出相。",
        "process_types": [],
    },
    "焊接电压": {
        "definition": "焊接电压（电弧电压）：焊接电弧两端的电压，决定弧长与熔宽。电压过高→弧长、飞溅大、咬边；过低→熔深浅、电弧不稳。",
        "application": "应用要点：与电流匹配（MIG 低碳钢约 20-27V）；机器人焊稳定电压控弧长。",
        "process_types": [],
    },
    "中间层": {
        "definition": "中间层：异种金属/陶瓷连接时夹在两待焊面之间的一层金属/合金（Ni/Cu/Ag/Ta 等），改善润湿、抑制脆性相、降低连接温度。",
        "application": "应用要点：按母材组合选中间层（钛-钢用 Ni/V，铝-钢用 Zn/Al）；TLP 用低熔点中间层。",
        "process_types": [],
    },
    "电弧焊": {
        "definition": "电弧焊：利用电弧热熔化金属进行焊接的方法总称，包括焊条电弧焊、埋弧焊、氩弧焊、气体保护焊、等离子焊等。是最主要的焊接方法。",
        "application": "应用要点：机器人主要用熔化极电弧焊（GMAW/FCAW）因可连续送丝；按材料/板厚选电弧焊种类。",
        "process_types": ["GMAW/MIG (熔化极氩弧焊)", "GTAW/TIG (钨极氩弧焊)"],
    },
    "坡口": {
        "definition": "坡口：为保证熔透，在焊件待焊端面加工成一定几何形状的沟槽（V 形/X 形/U 形等）。坡口形式/角度/钝边决定填充量与焊接工艺。",
        "application": "应用要点：按板厚选坡口（≤6 不开坡口/12 单 V/25 双 V）；坡口加工精度影响机器人焊缝跟踪。",
        "process_types": [],
    },
    "钝边": {
        "definition": "钝边：坡口根部保留的未开坡口的平直段（如 1-2mm），防止打底焊烧穿，保证根部熔透质量。",
        "application": "应用要点：钝边过大难熔透、过小易烧穿；机器人焊配合间隙控制根部成形。",
        "process_types": [],
    },
    "预热": {
        "definition": "预热：焊接前对焊件整体或局部加热到一定温度再施焊，目的降低冷却速度、防止冷裂、改善组织。按母材/板厚/碳当量确定。",
        "application": "应用要点：低碳钢一般不需；Q345>38mm 100-150°C、高强钢/中碳钢更高；机器人焊可配预热工装。",
        "process_types": [],
    },
    "MAG焊": {
        "definition": "MAG 焊（熔化极活性气体保护焊）：用活性气体（CO₂ 或 Ar+CO₂ 混合气）保护的熔化极电弧焊，焊丝连续送进。用于碳钢/低合金钢，成本低、效率高。",
        "application": "应用要点：机器人碳钢焊接常用 MAG（80%Ar+20%CO₂），ER50-6 焊丝，电流 100-300A。",
        "process_types": ["GMAW/MIG (熔化极氩弧焊)"],
    },
    "焊接电流": {
        "definition": "焊接电流：焊接回路中的电流，决定熔深/熔敷速度。电流大→熔深大、熔敷快，过大则烧穿/咬边；过小则未熔合。",
        "application": "应用要点：按板厚/焊丝直径选电流（1.2mm 焊丝约 120-280A）；机器人焊精确控流保证熔透。",
        "process_types": [],
    },
    "焊接速度": {
        "definition": "焊接速度：焊枪沿焊缝移动的速度，与电流电压共同决定线能量。过快→未熔合/咬边，过慢→热输入大变形。",
        "application": "应用要点：机器人焊可恒速精确控制，配合送丝匹配；按线能量要求调焊速。",
        "process_types": [],
    },
    "送丝机构": {
        "definition": "送丝机构：熔化极焊中连续推送焊丝进入熔池的装置（送丝轮/送丝管），送丝速度决定电流与熔敷率。",
        "application": "应用要点：机器人焊送丝机构稳定送丝（等速/变速送丝配合电源特性）；送丝不畅致断弧/飞溅。",
        "process_types": ["GMAW/MIG (熔化极氩弧焊)"],
    },
    "保护气": {
        "definition": "保护气：焊接中用于隔离空气、保护熔池与电弧的气体（Ar/He/CO₂/混合气）。选型影响熔滴过渡、成形与成本。",
        "application": "应用要点：MAG 用 80%Ar+20%CO₂、MIG 用纯Ar/富氩、CO₂ 焊纯 CO₂、钛/铝用纯 Ar；流量 15-25L/min 防风。",
        "process_types": [],
    },
    "钎焊": {
        "definition": "钎焊：用熔点低于母材的钎料熔化润湿母材，靠毛细作用填充接头间隙形成连接的方法。母材不熔化，变形小，适合异种材料/精密件。",
        "application": "应用要点：活性钎焊（Ag-Cu-Ti）连陶瓷-金属；机器人钎焊控温度/间隙/润湿；真空/气氛保护。",
        "process_types": [],
    },
    "电阻焊": {
        "definition": "电阻焊：利用电流通过工件接触面电阻产生焦耳热使局部熔化，加压形成连接（点焊/缝焊/凸焊）。无需填充金属、效率高，适合薄板大批量。",
        "application": "应用要点：机器人点焊是汽车车身焊接主力；控制电流/压力/时间保证焊点质量；电极损耗管理。",
        "process_types": [],
    },
    "焊缝跟踪": {
        "definition": "焊缝跟踪：机器人/自动化焊接中实时检测焊缝实际位置与坡口信息，纠偏焊枪轨迹的技术。用激光视觉/电弧传感/机械接触传感。",
        "application": "应用要点：激光视觉前置扫描坡口+电弧传感后置纠偏组合；管道/复杂轨迹焊接必需；提高一次合格率。",
        "process_types": [],
    },
    "焊接电源": {
        "definition": "焊接电源：为电弧提供电能的设备（恒流/恒压特性），分弧焊电源（直流/交流/脉冲）与电阻焊电源。特性匹配焊材送丝系统。",
        "application": "应用要点：机器人焊机（NBC-500RP）配恒压电源+等速送丝；脉冲电源控熔滴过渡减少飞溅。",
        "process_types": [],
    },
    "碳弧气刨": {
        "definition": "碳弧气刨：用碳棒与工件间电弧熔化金属，并用压缩空气吹除熔化金属，形成刨槽的工艺。用于清根、开坡口、去除缺陷。",
        "application": "应用要点：双面焊背面清根常用；返修去除缺陷后补焊；机器人可配合气刨自动化，注意烟尘防护。",
        "process_types": [],
    },
}

# ============================================================
# 手工应用拓展表（v2.6）
# 覆盖书中能搜到定义但"应用及拓展"提取不足的常见术语。
# 构建时若有此条目则补充 application。
# ============================================================
MANUAL_APPLICATIONS = {
    "熔池": "应用要点：熔池行为直接决定焊缝成形与缺陷（气孔/咬边）；机器人焊接中通过调整电流电压、焊速、送丝速度控制熔池尺寸与流动性，脉冲 MIG 可改善薄板熔池控制。",
    "电弧": "应用要点：电弧稳定性是焊接质量基础；机器人焊接常配脉冲电源（如 NBC-500RP）稳定电弧，电弧电压决定弧长，影响熔深与飞溅。",
    "热影响区": "应用要点：热影响区（HAZ）性能决定接头整体质量；通过控制热输入（线能量）、预热、层间温度、t8/5 冷却时间控制 HAZ 硬度与韧性，高强钢需防冷裂。",
    "裂纹": "应用要点：焊接裂纹分热裂/冷裂/再热裂；按母材选焊材、控制预热层间温度、减少拘束可预防；厚板高强钢需按 Ceq/Pcm 评估冷裂倾向。",
    "贝氏体": "应用要点：HAZ 贝氏体组织影响硬度和韧性；通过冷却速度控制贝氏体形态（粒状贝氏体韧性较好），多层多道焊可改善组织。",
    "咬边": "应用要点：咬边是常见成形缺陷，降低接头强度；预防：控制电流不过大、焊枪角度正确、适当摆动、薄板用短弧。",
    "未熔合": "应用要点：未熔合降低承载能力，是返修主要原因；预防：适当增大电流、降低焊速、保证熔池边缘充分熔化，多层焊控制层间清理。",
    "未焊透": "应用要点：未焊透减薄有效截面，多发生于打底焊；预防：控制坡口角度/间隙、保证打底电流充足、背面清根。",
    "气孔": "应用要点：气孔由氢/氮/CO 气体在熔池凝固前未逸出所致；预防：清理焊件、焊材烘干、气体保护流量充足、短弧操作。",
    "夹杂": "应用要点：夹渣/夹钨降低韧性；预防：多层焊每层清渣、正确运条、选用合适焊材。",
    "等轴晶": "应用要点：焊缝等轴晶区改善抗裂性；通过变质处理、控制冷却、搅拌（如超声波/磁场）细化晶粒。",
    "细晶区": "应用要点：HAZ 细晶区（FCCAZ）韧性最好；多层多道焊使晶粒细化，改善接头韧性。",
    "韧性": "应用要点：接头韧性受组织/杂质/残余应力影响；低温工况需关注 DBTT，通过控制热输入与焊材纯净度保证韧性。",
    "韧性断裂": "应用要点：韧性断裂（延性断裂）伴随明显塑性变形，比脆断安全；通过组织细化、减少夹杂提升抗韧性断裂能力。",
    "碳扩散": "应用要点：异种钢焊接（如珠光体钢+奥氏体钢）中碳从低合金侧向高合金侧扩散形成脱碳层/增碳层，降低接头性能；预防：用中间层（Ni 基）、控制焊后热处理。",
    "扩散焊": "应用要点：固态扩散连接用于高温合金/陶瓷/精密零件；需控制温度/压力/时间/真空度，TLP 用中间层降低要求。",
    "预热": "应用要点：预热降低冷却速度、防冷裂；低碳钢一般不需，高强钢/厚板按板厚与碳当量确定（Q345>38mm 需 100-150°C）。",
    "层间温度": "应用要点：多层多道焊控制层间温度（一般≤200-250°C）防过热与变形；不锈钢防敏化需≤150°C。",
    "焊后热处理": "应用要点：PWHT 消除残余应力、改善组织；受压件厚板按规范 600-650°C 去应力；高强钢需控温防止回火脆化。",
    "保护气": "应用要点：保护气类型影响熔滴过渡与成形：MAG 用 80%Ar+20%CO₂、MIG 用纯Ar/富氩、CO₂ 焊用纯CO₂；流量 15-25L/min 防风。",
    "氩弧焊": "应用要点：氩弧焊（GTAW/TIG）用钨极+氩气保护，成形好、无飞溅，适合薄板/有色金属/根部打底焊；机器人焊接中常用于管道打底、不锈钢薄板；注意钨极烧损、控制气体流量10-15L/min。",
    "电弧焊": "应用要点：电弧焊是熔化焊大类；机器人常用熔化极电弧焊（GMAW/FCAW）因熔敷效率高；控制电弧电压-电流匹配、送丝速度与焊速协同。",
    "药芯焊丝电弧焊": "应用要点：FCAW 药芯焊丝含造渣/造气成分，适合户外、大电流高速焊；机器人常用药芯焊丝提高效率，注意清渣与烟尘。",
    "堆焊": "应用要点：堆焊用于表面修复/耐磨层（D256/D322/D517/D707）；机器人堆焊控制稀释率与层间温度，多层多道逐层堆焊。",
    "镍基合金": "应用要点：镍基合金焊（Inconel 等）需控热输入防热裂、用 ERNiCr-3 等焊材；机器人焊接用于高温/耐蚀部件，控制层间温度≤150°C。",
    "线能量": "应用要点：线能量=电流×电压/焊速（kJ/mm），决定热输入与 HAZ 性能；机器人焊接通过调焊速/电流控制线能量，高强钢需限制上限。",
    "气焊": "应用要点：气焊用氧-乙炔焰，设备简单但效率低、变形大；机器人焊接基本不用，仅薄板/小件/补焊场景。",
    "碳弧气刨": "应用要点：碳弧气刨用于清根/开坡口/返修去除缺陷；机器人可配合气刨实现自动化返修，注意烟尘防护。",
    "摩擦焊": "应用要点：摩擦焊是固态连接，热影响区小、接头强度高，适合轴类/异种金属；机器人摩擦焊用于大批量轴件。",
    "电子束焊": "应用要点：电子束焊真空下高能量密度，深宽比大、热影响区极小，适合厚件/精密件；设备昂贵，机器人多用于焊缝对准。",
    "活性钎焊": "应用要点：活性钎焊（Ag-Cu-Ti）用于陶瓷-金属连接（DBC/SiC/AlN）；机器人精密定位钎焊，真空/气氛控制。",
    "脱碳层": "应用要点：异种钢/高温服役中碳迁移形成脱碳层（母材侧）与增碳层（焊缝侧），降低接头性能；预防：Ni 基中间层、限制焊后热处理温度时间。",
    "焊接速度": "应用要点：焊接速度影响线能量/成形/效率；机器人焊接可精确控制恒速，配合送丝速度/电流电压匹配；速度过快易咬边未熔合，过慢热输入大变形。",
    "焊条直径": "应用要点：焊条直径按板厚选（Φ3.2 常用 3-12mm、Φ4.0 用 5-25mm）；直径决定电流范围与熔敷率；机器人焊多用焊丝，直径 1.0-1.2mm。",
    "焊缝跟踪": "应用要点：焊缝跟踪是机器人焊接关键——通过激光/视觉/电弧传感器实时检测焊缝位置偏差并纠偏；常用激光视觉（前置）+ 电弧传感（后置）组合。",
    "V形坡口": "应用要点：V 形坡口用于中厚板对接，坡口角 60°±5°、钝边 1-2mm；机器人焊按坡口尺寸规划填充层数，保证根部熔透。",
    "X形坡口": "应用要点：X 形坡口用于厚板双面焊，减少填充量/变形；机器人焊先焊一面清根再焊另一面，控制变形对称。",
    "韧脆转变温度": "应用要点：DBTT/FATT 是低温韧性指标，低于转变温度材料变脆；焊接接头通过控热输入/组织细化提升低温韧性，压力容器低温工况关键。",
    "无损检测": "应用要点：焊缝质量检验：RT 射线/UT 超声/MT 磁粉/PT 渗透；厚板用 UT/RT，表面裂纹用 MT/PT；机器人焊后按标准抽检。",
    "氢脆": "应用要点：氢脆由扩散氢导致，高强钢敏感；预防：低氢焊材、焊前预热、焊后消氢处理（250°C×2h）、控制焊接环境湿度。",
    "冷裂纹敏感性": "应用要点：冷裂纹敏感性用碳当量 Ceq/Pcm 评估；高敏感钢需预热+控层间温度+后热，插销试验/Tekken 试验定量评估。",
    "再结晶": "应用要点：再结晶消除加工硬化/细化晶粒；多层多道焊热循环使 HAZ 再结晶改善韧性；控制热输入避免晶粒长大。",
    "热等静压": "应用要点：HIP（热等静压）消除内部孔隙/闭合裂纹，用于铸件/增材件/扩散焊件致密化；机器人焊接件关键承力部位可 HIP 处理。",
    "金相组织": "应用要点：金相检验评估焊缝/HAZ 组织（铁素体/贝氏体/马氏体/M-A）；SEM/TEM/EBSD 分析微观缺陷与析出相。",
    "局部脆化区": "应用要点：LBZ（局部脆化区）在 HAZ 临界区/粗晶区，韧性低；预防：控热输入、多层多道细化组织、选用高韧性焊材。",
    "部分熔化区": "应用要点：PMZ（部分熔化区）在熔合线附近，晶界偏析易产生液化裂纹；控制热输入、选低偏析焊材、异种钢用过渡层。",
}





try:
    from app.welding_knowledge_base import (
        TERM_ALIAS_MAP,
        DEEP_ANALYSIS,
        SCIENCE_POPULARIZATION,
        CROSS_DOMAIN_KNOWLEDGE,
        WELDING_PROCESS_PARAMS,
        MATERIAL_PARAM_MAP,
    )
except ImportError:
    TERM_ALIAS_MAP = {}
    DEEP_ANALYSIS = {}
    SCIENCE_POPULARIZATION = {}
    CROSS_DOMAIN_KNOWLEDGE = {}
    WELDING_PROCESS_PARAMS = {}
    MATERIAL_PARAM_MAP = {}


# ------------------------------------------------------------
# 工艺 / 材料 元数据（用于概念↔工艺/材料 交叉匹配）
# ------------------------------------------------------------
_PROCESS_LIST: List[dict] = []
for key, params in WELDING_PROCESS_PARAMS.items():
    if not isinstance(params, dict):
        continue
    # 从 "SMAW (焊条电弧焊)" 提取英文缩写 + 中文名
    m = re.match(r'([A-Z/]+)\s*[（(]\s*([^）)]+)\s*[)）]', key)
    abbr = m.group(1).strip() if m else key
    cn = m.group(2).strip() if m else key
    aliases = {key, abbr, cn, cn.replace('焊', ''), cn.replace('（', '').replace('）', '')}
    # 常见工程别名
    extra = {
        'SMAW': {'手工电弧焊', '手弧焊', '焊条焊'},
        'SAW': {'埋弧焊', '埋弧自动焊接', '埋弧'},
        'GTAW': {'TIG', '氩弧焊', '钨极氩弧焊', '钨极惰性气体焊'},
        'GMAW': {'MIG', 'MAG', '熔化极氩弧焊', 'CO2焊', 'CO₂焊', '二保焊', '气体保护焊'},
        'FCAW': {'药芯焊丝', '药芯焊丝焊', '自保护焊'},
        'PAW': {'等离子焊', '等离子弧', '微束等离子'},
    }.get(abbr.split('/')[0], set())
    aliases |= extra
    _PROCESS_LIST.append({
        "key": key, "abbr": abbr, "name": cn, "params": params, "aliases": {a for a in aliases if a},
    })

_MATERIAL_LIST: List[dict] = []
for key, params in MATERIAL_PARAM_MAP.items():
    if not isinstance(params, dict):
        continue
    aliases = {key, key.replace('（', '').replace('）', ''), key.split('(')[0].strip()}
    for brand in params.get("牌号", []):
        aliases.add(str(brand).split('(')[0].strip())
        aliases.add(str(brand))
    _MATERIAL_LIST.append({
        "key": key, "name": key, "params": params,
        "aliases": {a for a in aliases if a and len(str(a)) >= 2},
    })


class ExpertKnowledgeBase:
    """专家基座知识库 — 概念条目查询与构建"""

    def __init__(self, path: str = "saved_knowledge/expert_kb.json"):
        self.path = Path(path)
        self.concepts: Dict[str, dict] = {}  # canonical -> entry
        self.built_from: dict = {}
        self._alias_index: Dict[str, str] = {}  # alias -> canonical

    # ------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------
    def build(self, store=None) -> dict:
        """从基座常量 + 已学书籍构建概念条目库，返回统计"""
        canonicals = list(TERM_ALIAS_MAP.keys())
        if not canonicals:
            canonicals = ["焊接", "焊缝", "熔池", "电弧"]
        self.concepts = {}
        self._alias_index = {}

        # 预取章节（一次读取所有书所有章，避免每次 search_across_sources）
        chapters_by_source = self._collect_chapters(store)

        for canonical in canonicals:
            canonical_s = str(canonical).strip()
            if not canonical_s:
                continue
            entry = self._build_concept(canonical_s, chapters_by_source, store)
            self.concepts[canonical_s] = entry
            for al in entry.get("aliases", []):
                self._alias_index.setdefault(str(al).strip(), canonical_s)

        self.save()
        return {
            "concepts": len(self.concepts),
            "alias_index": len(self._alias_index),
            "built_from": self.built_from,
        }

    def _collect_chapters(self, store) -> Dict[str, list]:
        """{source_name: [chapter_dict]}"""
        out = {}
        if store is None:
            return out
        try:
            for src in store.list_sources():
                chs = store.get_chapters(src["id"])
                if chs:
                    out[src["filename"]] = chs
        except Exception as e:
            logger.warning(f"collect chapters failed: {e}")
        return out

    def _build_concept(self, canonical: str, chapters_by_source: dict, store) -> dict:
        aliases = [str(a) for a in TERM_ALIAS_MAP.get(canonical, []) if str(a).strip()]
        all_terms = [canonical] + aliases
        id_ = f"concept_{canonical}"

        # ---- 0. 手工定义优先（覆盖书中未收录/专业词/缩写/衍生概念）----
        manual = MANUAL_DEFINITIONS.get(canonical)
        if manual is None:
            # 别名反查：手工表 key 若命中某个 term（如 熔滴过渡 命中 canonical=熔滴 的别名），
            # 也用手工定义（更贴合用户问法）
            for term in all_terms:
                if term in MANUAL_DEFINITIONS:
                    manual = MANUAL_DEFINITIONS[term]
                    break
        if manual:
            definition = manual.get("definition", "")
            application = manual.get("application", "")
            process_types = manual.get("process_types", [])
        else:
            # ---- 1. 概念解析 definition ----
            definition = self._gather_definition(canonical, all_terms, chapters_by_source, store)

            # ---- 2. 应用及拓展 application ----
            application = self._gather_application(canonical, all_terms, chapters_by_source)

            # ---- 3. 支持的大体工艺类型 ----
            process_types = self._match_processes(all_terms)

        # ---- 手工应用补充：若手工应用表有该概念，优先用手工（保证质量与相关性）----
        if canonical in MANUAL_APPLICATIONS:
            application = MANUAL_APPLICATIONS[canonical]

        # ---- 4. 工艺/材料参数 ----
        process_params = None
        material_params = None
        for p in _PROCESS_LIST:
            if self._terms_overlap(all_terms, p["aliases"] | {p["name"], p["abbr"], p["key"]}):
                process_params = p["params"]
                break
        for mat in _MATERIAL_LIST:
            if self._terms_overlap(all_terms, mat["aliases"] | {mat["name"]}):
                material_params = mat["params"]
                break

        # ---- 5. 来源（引用 PDF 上传书籍章节） ----
        sources = self._gather_sources(canonical, all_terms, chapters_by_source)

        # ---- 6. 关键词 ----
        keywords = all_terms + [a for a in aliases if len(a) >= 2]
        keywords = list(dict.fromkeys(keywords))[:20]

        return {
            "id": id_,
            "name": f"{canonical}",
            "canonical": canonical,
            "aliases": aliases,
            "definition": definition,
            "application": application,
            "process_types": process_types,
            "process_params": process_params,
            "material_params": material_params,
            "keywords": keywords,
            "sources": sources,
        }

    def _gather_definition(self, canonical, all_terms, chapters_by_source, store) -> str:
        # 1) DEEP_ANALYSIS 深度分析
        for topic_key, data in DEEP_ANALYSIS.items():
            if not isinstance(data, dict):
                continue
            title = str(data.get("title", ""))
            if any(t in canonical or canonical in t for t in [topic_key, title]) or any(
                    a in title or title in a for a in all_terms if len(a) >= 2):
                parts = [f"## {data.get('title', canonical)}", data.get("overview", "")]
                for sec_title, sec in (data.get("sections", {}) or {}).items():
                    parts.append(f"### {sec_title}\n{sec}")
                return "\n\n".join([p for p in parts if p])

        # 2) 章节摘要命中：用清洗后的章节摘要作为定义（干净，不抓 OCR 原文段落）
        terms = [t for t in all_terms if len(str(t)) >= 2]
        best = None  # (count, src_name, chapter)
        for src_name, chs in chapters_by_source.items():
            for ch in chs:
                if self._is_noise_title(ch.get("title", "")):
                    continue
                summary = ch.get("summary", "") or ""
                if self._is_garbled(summary):
                    continue
                content = ch.get("content", "") or ""
                # 优先 canonical 精确计数；canonical 不出现才用 aliases
                count = content.count(str(canonical))
                if count == 0:
                    count = sum(content.count(str(t)) for t in terms if t != str(canonical))
                if count > 0 and (best is None or count > best[0]):
                    best = (count, src_name, ch, summary)
        if best:
            count, src_name, ch, summary = best
            return f"据《{src_name}》「{ch.get('title','')}」：{summary}"

        # 3) 章节关键词/摘要命中（内容未命中时回退）
        hits = []
        for src_name, chs in chapters_by_source.items():
            for ch in chs:
                if self._is_noise_title(ch.get("title", "")):
                    continue
                ch_kws = ch.get("keywords", []) or []
                if any(t in ch_kws for t in terms):
                    hits.append(f"据《{src_name}》「{ch.get('title','')}」：{ch.get('summary','')[:200]}")
                if len(hits) >= 3:
                    break
            if len(hits) >= 3:
                break
        if hits:
            return "\n\n".join(hits)

        # 4) 兜底：跨源搜索
        if store is not None:
            try:
                matches = store.search_across_sources(canonical)
                if matches:
                    top = matches[0]
                    return f"据《{top['source']}》「{top['chapter']}」：{top.get('summary','')[:300]}"
            except Exception:
                pass
        return "（暂无该概念的权威定义，可参阅《材料焊接原理》相关章节。）"

    @staticmethod
    def _extract_context(content: str, canonical: str, terms: list, width: int = 280) -> str:
        """从章节内容中提取概念词附近的上下文，作为定义片段。
        用滑动窗口找 canonical 出现最密集的窗口（主题展开处，而非总论顺带提及）。"""
        target = canonical if canonical in content else next((t for t in terms if t in content), None)
        if not target:
            return content[:width]
        # 找 canonical 出现最密集的 500 字窗口
        positions = []
        start_i = 0
        while True:
            i = content.find(target, start_i)
            if i < 0:
                break
            positions.append(i)
            start_i = i + 1
        if not positions:
            return content[:width]
        # 统计每个位置前后 250 字窗口内 target 出现次数，取最密集
        best_pos, best_cnt = positions[0], 0
        for pos in positions:
            win = content[max(0, pos - 200):pos + 300]
            cnt = win.count(target)
            if cnt > best_cnt:
                best_cnt, best_pos = cnt, pos
        idx = best_pos
        # 向前截到最近句号/换行（避免带上不同主题前文）
        start = max(0, idx - 60)
        head = content[max(0, idx - 100):idx]
        for sep in ("。", "；", "\n"):
            pos = head.rfind(sep)
            if pos >= 0:
                start = max(0, idx - 100 + pos + 1)
                break
        end = min(len(content), idx + len(target) + width - 60)
        snippet = content[start:end].strip()
        return ExpertKnowledgeBase._clean_snippet(snippet)

    @staticmethod
    def _clean_snippet(text: str, width: int = 400) -> str:
        """清理定义/应用片段：剔除 [Page N] 页标记、页眉页脚、孤立图注行、多余空行"""
        if not text:
            return text
        import re as _re
        lines = []
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            # 剔除 [Page N] 页标记
            if _re.fullmatch(r'\[Page\s*\d+\]', s):
                continue
            # 剔除孤立页眉行（如 第三章其他电弧焊方法 155 / 第三章其他电弧焊方法 / 焊接手册 123）
            if _re.fullmatch(r'第[一二三四五六七八九十\d]+[章节][^\n]{0,30}(\s*\d{1,4})?', s):
                continue
            if _re.fullmatch(r'[一-鿿]{2,20}\s*\d{1,4}', s) and len(s) <= 12:
                continue
            # 剔除图注行（图3-30xxx / 图1-9 表面活性剂...）
            if _re.match(r'^图\s*\d+[-－]?\d+', s):
                continue
            # 剔除零件号行（1-直流电源；2-控制箱 / 5一送丝机构；6一焊丝）
            if _re.match(r'^[\d一二三四五六七八九十]+[-\-一][一-鿿]', s):
                continue
            # 剔除孤立数字行（如 155 / 3mm 但短）
            if _re.fullmatch(r'\d+(\.\d+)?(mm|A|V)?', s) and len(s) <= 8:
                continue
            lines.append(s)
        out = "\n".join(lines)
        # 去除连续空白行
        out = _re.sub(r'\n{2,}', '\n', out)
        return out[:width]

    def _gather_application(self, canonical, all_terms, chapters_by_source) -> str:
        parts = []
        # 1) CROSS_DOMAIN 实践指导
        for cross_key, data in CROSS_DOMAIN_KNOWLEDGE.items():
            if not isinstance(data, dict):
                continue
            name = str(data.get("name", ""))
            if any(t in name or name in t for t in all_terms if len(t) >= 2):
                parts.append(f"## {name}\n{data.get('description','')}")
                pg = data.get("practical_guidance", {})
                if pg:
                    parts.append("### 实践指导\n" + "\n".join(f"- {k}：{v}" for k, v in pg.items()))
        # 2) 章节实践要点（摘要 + 关键词）— 过滤乱码 + 按相关度排序 + 限条数
        scored = []
        for src_name, chs in chapters_by_source.items():
            for ch in chs:
                if self._is_noise_title(ch.get("title", "")):
                    continue
                ch_kws = ch.get("keywords", []) or []
                summary = ch.get("summary", "") or ""
                if self._is_garbled(summary):
                    continue
                kw_hit = [t for t in all_terms if len(str(t)) >= 2 and t in ch_kws]
                if kw_hit:
                    title = ch.get("title", "")
                    score = len(kw_hit) * 3
                    scored.append((score, f"据《{src_name}》「{title}」：{summary[:150]}"))
        # 按相关度降序，最多取 3 条
        scored.sort(key=lambda x: -x[0])
        parts.extend(s for _, s in scored[:3])
        return "\n\n".join(parts)[:900]

    def _match_processes(self, all_terms) -> list:
        out = []
        for p in _PROCESS_LIST:
            if self._terms_overlap(all_terms, p["aliases"] | {p["name"], p["abbr"], p["key"]}):
                out.append(p["key"])
        return out

    def _gather_sources(self, canonical, all_terms, chapters_by_source) -> list:
        """来源：章节关键词命中（优先）+ 章节内容命中（召回）。
        过滤乱码章节，关键词命中优先排序，限制数量。"""
        sources = []
        seen = set()
        terms = [t for t in all_terms if len(str(t)) >= 2]
        scored = []
        for src_name, chs in chapters_by_source.items():
            for ch in chs:
                title = ch.get("title", "")
                if title in seen or self._is_noise_title(title):
                    continue
                ch_kws = ch.get("keywords", []) or []
                content = ch.get("content", "") or ""
                if self._is_garbled(content):
                    continue
                kw_hit = any(t in ch_kws for t in terms)
                content_hit = any(t in content for t in terms)
                if kw_hit or content_hit:
                    seen.add(title)
                    # 相关度评分：
                    #  - 章节标题含 canonical → 高权重（主题章）
                    #  - 关键词命中 → 中权重
                    #  - canonical 内容密度（次数/长度）→ 主题展开程度
                    title_hit = str(canonical) in title or any(t in title for t in terms if len(t) >= 3)
                    kw_count = sum(1 for t in terms if t in ch_kws)
                    canon_count = content.count(str(canonical))
                    density = canon_count / max(len(content), 1) * 10000  # 每万字次数
                    score = (30 if title_hit else 0) + kw_count * 6 + min(density, 20)
                    scored.append((score, {
                        "book": src_name,
                        "chapter": title,
                        "page_hint": ch.get("page_hint", ""),
                        "match": "keyword" if kw_hit else "content",
                    }))
        # 按相关度降序，取前8
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:8]]

    @staticmethod
    def _is_noise_title(title: str) -> bool:
        """OCR 噪声标题过滤：中文占比过低且较短（如 '1800      下     KL'）"""
        if not title:
            return True
        t = str(title).strip()
        cn = sum(1 for c in t if '一' <= c <= '鿿')
        ratio = cn / max(len(t), 1)
        return ratio < 0.3 and len(t) < 20

    @staticmethod
    def _is_garbled(text: str, min_cn_ratio: float = 0.55) -> bool:
        """OCR 乱码过滤：中文占比低于阈值视为乱码（如焊接结构原理的噪声页）"""
        if not text:
            return True
        t = str(text)
        # 只统计有意义的片段（去空白）
        meaningful = re.sub(r'\s+', '', t)
        if not meaningful:
            return True
        cn = sum(1 for c in meaningful if '一' <= c <= '鿿')
        ratio = cn / len(meaningful)
        return ratio < min_cn_ratio

    @staticmethod
    def _terms_overlap(a: list, b: set) -> bool:
        for t in a:
            ts = str(t).strip()
            if not ts or len(ts) < 2:
                continue
            if ts in b:
                return True
            for bb in b:
                if len(str(bb)) >= 2 and (ts in str(bb) or str(bb) in ts):
                    return True
        return False

    # ------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------
    def get_concept(self, canonical: str) -> Optional[dict]:
        return self.concepts.get(canonical)

    def lookup(self, keywords: list) -> Optional[dict]:
        """按关键词命中概念条目：先规范词，再别名反查，再子串模糊"""
        for kw in keywords or []:
            kw_s = str(kw).strip()
            if kw_s in self.concepts:
                return self.concepts[kw_s]
        for kw in keywords or []:
            kw_s = str(kw).strip()
            if kw_s in self._alias_index:
                return self.concepts.get(self._alias_index[kw_s])
        # 模糊：概念名是关键词子串
        for kw in keywords or []:
            kw_s = str(kw).strip()
            if len(kw_s) < 2:
                continue
            for canonical in self.concepts:
                if len(canonical) >= 2 and canonical in kw_s:
                    return self.concepts[canonical]
        return None

    def match_concepts(self, fragments: list) -> list:
        """模糊匹配：返回 [{concept, score}]"""
        out = []
        for frag in fragments or []:
            fs = str(frag).strip()
            if len(fs) < 2:
                continue
            for canonical, entry in self.concepts.items():
                if fs in canonical:
                    out.append({"canonical": canonical, "score": 1.0})
                elif any(fs in str(a) for a in entry.get("aliases", [])):
                    out.append({"canonical": canonical, "score": 0.9})
        # 去重
        uniq = {}
        for o in out:
            c = o["canonical"]
            if c not in uniq or o["score"] > uniq[c]["score"]:
                uniq[c] = o
        return sorted(uniq.values(), key=lambda x: -x["score"])[:10]

    # ------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------
    def save(self, path: str = None):
        p = Path(path) if path else self.path
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "concepts": self.concepts,
            "built_from": self.built_from,
        }
        tmp = p.with_suffix(".tmp.json")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    def load(self) -> bool:
        if not self.path.exists():
            return False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.concepts = data.get("concepts", {})
            self.built_from = data.get("built_from", {})
            self._alias_index = {}
            for canonical, entry in self.concepts.items():
                for al in entry.get("aliases", []):
                    self._alias_index.setdefault(str(al).strip(), canonical)
            return bool(self.concepts)
        except Exception as e:
            logger.warning(f"expert_kb load failed: {e}")
            return False

    def stats(self) -> dict:
        return {"concepts": len(self.concepts), "alias_index": len(self._alias_index)}


# ------------------------------------------------------------
# 单例
# ------------------------------------------------------------
_kb: Optional[ExpertKnowledgeBase] = None


def get_expert_kb(path: str = "saved_knowledge/expert_kb.json") -> ExpertKnowledgeBase:
    global _kb
    if _kb is None:
        _kb = ExpertKnowledgeBase(path)
    return _kb

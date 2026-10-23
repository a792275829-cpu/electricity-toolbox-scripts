"""Runtime administrative hierarchy, reviewed against the supplied city list.

Source: 广东500kV节点按市分类.xlsx; four unresolved names confirmed by the
user on 2026-10-23. Keep topology names and A/B/C identifiers unchanged.
This is feature configuration, independent of local workbooks and price data.
"""

REGIONS = (
    ("珠三角地区", ("广州", "深圳", "珠海", "佛山", "惠州", "东莞", "中山", "江门", "肇庆")),
    ("粤东地区", ("汕头", "潮州", "揭阳", "汕尾")),
    ("粤西地区", ("湛江", "茂名", "阳江")),
    ("粤北地区", ("韶关", "河源", "梅州", "清远", "云浮")),
)

USER_CONFIRMED_CITIES = {
    "徐闻开关站": "湛江", "玄陆升压站": "汕尾",
    "铜鼓电厂": "江门", "阳西A厂": "阳江",
}

CITY_NODES = {
    "广州": "从西换流站、北郊站、增城站、广南变、木棉站、楚庭站、狮洋站、科北站、穗东换流站、花都站、荔西站",
    "深圳": "东方换流站、大亚湾电厂、宝安换流站、屹百站、岭澳核电厂、征程站、深圳站、现代站、紫荆站、鲲鹏站、鹏城站",
    "珠海": "加林站、国安站、珠海B厂、金鼎站",
    "佛山": "东坡站、凤城站、沧江站、罗洞站、西江变、顺德站",
    "惠州": "博罗变、太平岭核电厂、崇文站、平海电厂、惠州站、惠蓄电厂、渡水站、演达变、祯州站、福园站、龙门换流站",
    "东莞": "东莞变、南粤换流站、崇焕站、横沥站、水乡站、盛丰站、纵江站、莞城站",
    "中山": "文山站、桂山站、香山站",
    "江门": "五邑站、侨乡换流站、圭峰站、江门变、襟岛核电厂、鳌峰站、铜鼓电厂",
    "肇庆": "柑乡变、玉城站、砚都站、肇庆换流站",
    "汕头": "汕头变、海门电厂、澄海站、胪岗站",
    "潮州": "柘林电厂、韩江站",
    "揭阳": "岐山站、榕江站、盘龙站、靖海电厂",
    "汕尾": "吉康站、小漠电厂、甲湖湾电厂、红海湾电厂、茅湖站、融珠站、陆丰站、玄陆升压站",
    "湛江": "东海岛站、乌石湾电厂、凤翔站、安澜变、港城变、芷寮站、调顺电厂、徐闻开关站",
    "茂名": "博贺电厂、电白站、茂名站",
    "阳江": "合和海风场陆上站、回隆变、峡阳海风场、海路陆上集控站、蝶岭站、阳江核电厂、阳西A厂、阳西B厂、阳西C厂、鹅凰A站、鹅凰B站、龙田变",
    "韶关": "丹霞站、曲江站",
    "河源": "上寨站、凤安站、承龙开关站",
    "梅州": "云霄换流站、嘉应站、敬州变",
    "清远": "园山电厂、库湾站、振兴站、贤令山站",
    "云浮": "卧龙变、立安变",
}
NODE_CITY = {node: city for city, names in CITY_NODES.items() for node in names.split("、")}


def price_hierarchy(names):
    names = set(names)
    result = [{"name": region, "cities": [
        {"name": city, "nodes": [n for n in CITY_NODES[city].split("、") if n in names]}
        for city in cities]} for region, cities in REGIONS]
    unknown = sorted(names - set(NODE_CITY))
    if unknown:
        result.append({"name": "未分类", "cities": [{"name": "待确认", "nodes": unknown}]})
    return result


def city_pair_label(a, b):
    """Display endpoint cities in topology order, collapsing a shared city."""
    return "-".join(dict.fromkeys(NODE_CITY.get(name, "未分类") for name in (a, b)))

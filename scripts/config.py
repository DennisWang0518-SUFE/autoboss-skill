CONFIG = {
    # 目标页面：在 Chrome 中筛选好条件后，把地址栏 URL 粘贴到这里
    # 示例：搜索"AI产品经理"，经验1-3年，北京，薪资15-25K
    # "target_url": "https://www.zhipin.com/web/geek/jobs?city=100010000&salary=406&experience=102,103,101&scale=303,304,305,306&query=ai%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86",
    "target_url": "https://www.zhipin.com/web/geek/jobs?salary=406&experience=102,103,101&scale=303,304,305,306",
    "cdp_endpoint": "http://localhost:9222",

    # 投递数量控制
    "max_jobs_per_run": 150,            # 每次运行最多投递数（建议首次先设小一些）
    "max_jobs_per_session": 20,      # 每批后暂停，模拟人类行为

    # 延迟设置（秒）
    "delay_between_jobs": (2.0, 5.0),    # 点击职位卡片间隔（随机区间）
    "delay_after_contact": (1.0, 3.0),   # 点击"立即沟通"后等待
    "session_break": (30, 60),           # 批间长暂停

    # 功能开关
    "dry_run": False,   # 首次运行建议 True（只点卡片不投递），确认选择器正常后改 False
    "resume": True,    # True=跳过已投递的职位（推荐保持 True）

    # 超时设置（毫秒）——网络慢时可适当增大
    "page_timeout": 15000,
    "dialog_timeout": 8000,
    "element_timeout": 5000,
}

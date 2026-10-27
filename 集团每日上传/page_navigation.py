"""Group daily upload: day-report page navigation and report creation.

The entry script injects ``log``, ``short_error`` and ``DIAGNOSTICS_DIR`` so the
GUI log sink and the managed data directory stay owned by the entry point.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from upload_files import daily_report_name, validate_date

BASE_URL = "https://xhxt.chng.com.cn"
HUANENG_GROUP_URL = f"{BASE_URL}/huaneng/group"
REPORT_URL = f"{BASE_URL}/huaneng/dataManagement/Report"
DAY_REPORT_URL = f"{BASE_URL}/huaneng/report/dayReport"
USERCENTER_URL = f"{BASE_URL}/usercenter/#/"
DEFAULT_DAILY_REPORT_TEMPLATE = "广东模板（修改）"
DEFAULT_DAILY_REPORT_TEMPLATE_ID = "e4dccaa498651f38019877e865fe0022"
REPORT_API_TIMEOUT_MS = 60000
DAY_REPORT_TENANT_NAMES = ("广东分公司", "中国华能集团有限公司广东分公司", "华能广东分公司")
DIAGNOSTICS_DIR = Path.cwd()


def log(message: str) -> None:
    print(message, flush=True)


def short_error(exc: BaseException) -> str:
    return str(exc).split("Call log:")[0].strip()


def parse_json_response(response, label: str) -> dict[str, Any]:
    text = response.text()
    if response.status >= 400:
        raise RuntimeError(f"{label} HTTP {response.status}: {text[:300]}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} 返回的不是 JSON，可能登录已失效。片段：{text[:200]}") from exc
    if data.get("retCode") and data.get("retCode") != "T200":
        raise RuntimeError(f"{label} 失败：{data.get('retMsg') or data}")
    return data


def json_or_text(response) -> Any:
    text = response.text()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def body_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=5000)
    except PlaywrightError:
        return ""


def page_needs_manual_navigation(page: Page) -> bool:
    text = body_text(page)
    url = page.url
    title = page.title()
    return (
        "/Exception/403" in url
        or "403" == title
        or "没有访问权限" in text
        or "去登录" in text
        or "/login" in url
    )


def find_report_page(context, fallback: Page) -> Page:
    for page in context.pages:
        if "/huaneng/dataManagement/Report" in page.url and not page_needs_manual_navigation(page):
            return page
    return fallback


def select_day_report_tenant(tenants: list[dict[str, Any]]) -> dict[str, Any] | None:
    for target_name in DAY_REPORT_TENANT_NAMES:
        for tenant in tenants:
            if str(tenant.get("name") or "").strip() == target_name and tenant.get("tenantId"):
                return tenant
    for tenant in tenants:
        name = str(tenant.get("name") or "").strip()
        if "广东分公司" in name and tenant.get("tenantId"):
            return tenant
    return None


def load_day_report_application_tenants(context) -> list[dict[str, Any]]:
    response = context.request.get(
        f"{BASE_URL}/usercenter/web/pf/tenant/user/application",
        timeout=20000,
    )
    data = parse_json_response(response, "读取应用大厅授权")
    tenants = data.get("data") or []
    if not isinstance(tenants, list):
        raise RuntimeError("读取应用大厅授权返回格式不正确。")
    return tenants


def switch_to_day_report_tenant(context) -> None:
    tenant = select_day_report_tenant(load_day_report_application_tenants(context))
    if not tenant:
        response = context.request.get(
            f"{BASE_URL}/gdfire/api/pf/tenant/user/tenant/grantApplication",
            timeout=20000,
        )
        data = parse_json_response(response, "读取授权单位")
        tenants = data.get("data") or []
        if not isinstance(tenants, list):
            raise RuntimeError("读取授权单位返回格式不正确。")
        tenant = select_day_report_tenant(tenants)
    if not tenant:
        log("应用大厅授权和授权单位里都没有找到“广东分公司”，跳过租户切换。")
        return
    tenant_id = str(tenant["tenantId"])
    tenant_name = str(tenant.get("name") or tenant_id)
    log(f"正在切换到日报管理单位：{tenant_name}")
    switch_response = context.request.get(
        f"{BASE_URL}/usercenter/web/switchTenant",
        params={"tenantId": tenant_id},
        timeout=20000,
    )
    if switch_response.status >= 400:
        raise RuntimeError(f"切换日报管理单位失败 HTTP {switch_response.status}")


def open_report_page(context, *, allow_manual: bool) -> Page:
    page = context.new_page()
    log(f"正在打开日报数据管理页面：{REPORT_URL}")
    page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass

    if page_needs_manual_navigation(page):
        if not allow_manual:
            raise RuntimeError("未能直接进入“日报数据管理”页面。请先在工具箱中重新登录日报系统，并确认该浏览器能访问日报数据管理。")
        log("\n当前页面未直接进入日报数据管理。")
        log("请在打开的浏览器窗口中登录，或从首页进入“日报数据管理”页面；完成后回到这里按 Enter。")
        input()
        page = find_report_page(context, page)
        if page_needs_manual_navigation(page):
            raise RuntimeError("仍未进入可访问的“日报数据管理”页面，无法继续上传。")

    return page


def enter_huaneng_marketing_platform(page: Page) -> None:
    log(f"正在打开应用大厅：{USERCENTER_URL}")
    page.goto(USERCENTER_URL, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass

    clicked = page.evaluate(
        """
        () => {
          const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
          const compact = s => String(s || '').replace(/\\s+/g, '').trim();
          const targetText = '华能集团电力营销管控平台';
          const nodes = [...document.querySelectorAll('a,button,[role="button"],div,span,li')].filter(visible);
          const target = nodes.find(el => compact(el.innerText || el.textContent || '').includes(targetText));
          if (!target) return false;
          let clickable = target;
          for (let node = target; node && node !== document.body; node = node.parentElement) {
            const style = window.getComputedStyle(node);
            if (
              node.tagName === 'A' ||
              node.tagName === 'BUTTON' ||
              node.getAttribute('role') === 'button' ||
              typeof node.onclick === 'function' ||
              style.cursor === 'pointer'
            ) {
              clickable = node;
              break;
            }
          }
          clickable.click();
          return true;
        }
        """
    )
    if not clicked:
        raise RuntimeError("应用大厅里没有找到“华能集团电力营销管控平台”入口。")
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(1000)


def open_day_report_page(context, *, allow_manual: bool) -> Page:
    page = context.new_page()
    try:
        switch_to_day_report_tenant(context)
    except Exception as exc:
        log(f"切换日报管理单位失败，将继续尝试直接打开页面：{short_error(exc)}")
    log(f"正在打开日报管理页面：{DAY_REPORT_URL}")
    page.goto(DAY_REPORT_URL, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass

    if page_needs_manual_navigation(page):
        log("直接进入日报管理失败，从应用大厅进入华能集团电力营销管控平台后重试。")
        try:
            enter_huaneng_marketing_platform(page)
            log(f"正在重新打开日报管理页面：{DAY_REPORT_URL}")
            page.goto(DAY_REPORT_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except (PlaywrightTimeoutError, PlaywrightError):
            pass
        except RuntimeError as exc:
            log(str(exc))
    if page_needs_manual_navigation(page):
        if not allow_manual:
            text = body_text(page)
            raise RuntimeError(
                "已进入同站点日报数据管理，但未能切换到“日报管理”页面。"
                f"当前URL：{page.url}；页面标题：{page.title()}；页面文本片段：{text[:300]}"
            )
        log("\n当前页面未直接进入日报管理。")
        log("请在打开的浏览器窗口中登录，或手动进入“日报管理”页面；完成后回到这里按 Enter。")
        input()
        for candidate in context.pages:
            if "/huaneng/report/dayReport" in candidate.url and not page_needs_manual_navigation(candidate):
                return candidate
        if page_needs_manual_navigation(page):
            raise RuntimeError("仍未进入可访问的“日报管理”页面，无法继续新建报表。")

    return page


def click_button_by_text(page: Page, text: str, *, timeout: int = 15000) -> None:
    locators = [
        page.get_by_role("button", name=re.compile(rf"^\s*{re.escape(text)}\s*$")),
        page.locator(f"button:has-text('{text}')"),
        page.locator(f"text={text}"),
    ]
    last_error: BaseException | None = None
    for locator in locators:
        try:
            locator.first.click(timeout=timeout)
            return
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            last_error = exc
    raise RuntimeError(f"没有找到“{text}”按钮。") from last_error


def set_form_input_by_label(page: Page, label: str, value: str) -> None:
    updated = page.evaluate(
        """
        ({ label, value }) => {
          const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
          const compact = s => String(s || '').replace(/\\s+/g, ' ').trim();
          const normalize = s => compact(s).replace(/模版/g, '模板').replace(/[：:*\\s]/g, '');
          const target = normalize(label);
          const labels = [...document.querySelectorAll('label,.el-form-item__label,.ant-form-item-label,[class*="form-item-label"]')]
            .filter(el => visible(el) && normalize(el.innerText || el.textContent).includes(target))
            .sort((a, b) => {
              const aText = normalize(a.innerText || a.textContent);
              const bText = normalize(b.innerText || b.textContent);
              return (aText === target ? 0 : 1) - (bText === target ? 0 : 1) || aText.length - bText.length;
            });
          for (const labelEl of labels) {
            let node = labelEl;
            for (let depth = 0; node && depth < 5; depth++, node = node.parentElement) {
              const input = [...node.querySelectorAll('input,textarea')]
                .find(el => visible(el) && !el.disabled && !el.readOnly);
              if (!input) continue;
              const setter = Object.getOwnPropertyDescriptor(
                input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
                'value'
              ).set;
              setter.call(input, value);
              input.dispatchEvent(new Event('input', { bubbles: true }));
              input.dispatchEvent(new Event('change', { bubbles: true }));
              input.blur();
              return true;
            }
          }
          return false;
        }
        """,
        {"label": label, "value": value},
    )
    if not updated:
        raise RuntimeError(f"没有找到“{label}”输入框。")
    page.wait_for_timeout(300)


def select_form_option_by_label(page: Page, label: str, option_text: str) -> None:
    marked = page.evaluate(
        """
        ({ label }) => {
          const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
          const compact = s => String(s || '').replace(/\\s+/g, ' ').trim();
          const normalize = s => compact(s).replace(/模版/g, '模板').replace(/[：:*\\s]/g, '');
          const target = normalize(label);
          document.querySelectorAll('[data-codex-form-select]').forEach(el => el.removeAttribute('data-codex-form-select'));
          const labels = [...document.querySelectorAll('label,.el-form-item__label,.ant-form-item-label,[class*="form-item-label"]')]
            .filter(el => visible(el) && normalize(el.innerText || el.textContent).includes(target))
            .sort((a, b) => {
              const aText = normalize(a.innerText || a.textContent);
              const bText = normalize(b.innerText || b.textContent);
              return (aText === target ? 0 : 1) - (bText === target ? 0 : 1) || aText.length - bText.length;
            });
          for (const labelEl of labels) {
            let node = labelEl;
            for (let depth = 0; node && depth < 5; depth++, node = node.parentElement) {
              const field = [...node.querySelectorAll('input,[role="combobox"],.el-select,.ant-select,[class*="select"]')]
                .find(el => visible(el) && !el.disabled);
              if (!field) continue;
              const selectRoot = field.closest('.ant-select,.el-select') || field;
              selectRoot.setAttribute('data-codex-form-select', 'true');
              return compact(selectRoot.value || selectRoot.innerText || selectRoot.textContent || field.value || '');
            }
          }
          return null;
        }
        """,
        {"label": label},
    )
    if marked is None:
        raise RuntimeError(f"没有找到“{label}”下拉框。")
    if option_text in str(marked):
        return
    select_locator = page.locator('[data-codex-form-select="true"]').first
    select_locator.click(timeout=10000)
    search_input = page.locator(
        '[data-codex-form-select="true"] input[role="combobox"], '
        '[data-codex-form-select="true"] input, '
        '[data-codex-form-select="true"][role="combobox"], '
        '[data-codex-form-select="true"]'
    ).first
    try:
        search_input.fill(option_text, timeout=5000, force=True)
    except (PlaywrightTimeoutError, PlaywrightError):
        pass
    page.wait_for_timeout(500)
    visible_dropdown_option = page.locator(
        ".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option"
    ).filter(has_text=option_text)
    try:
        visible_dropdown_option.last.click(timeout=10000)
    except (PlaywrightTimeoutError, PlaywrightError):
        try:
            search_input.press("Enter", timeout=5000)
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            raise RuntimeError(f"没有找到“{option_text}”选项。") from exc
    page.wait_for_timeout(300)


def find_daily_report_template_id(context, template_name: str = DEFAULT_DAILY_REPORT_TEMPLATE) -> str:
    last_error: BaseException | None = None
    response = None
    for attempt in range(1, 4):
        try:
            response = context.request.get(
                f"{BASE_URL}/huaneng/group/api/report/queryTemplates",
                timeout=REPORT_API_TIMEOUT_MS,
            )
            break
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            last_error = exc
            log(f"读取日报模板列表超时/失败，准备重试（{attempt}/3）：{short_error(exc)}")
            time.sleep(2)
    if response is None:
        if template_name == DEFAULT_DAILY_REPORT_TEMPLATE:
            log(f"读取日报模板列表失败，使用已验证模板ID兜底：{DEFAULT_DAILY_REPORT_TEMPLATE_ID}")
            return DEFAULT_DAILY_REPORT_TEMPLATE_ID
        raise RuntimeError(f"读取日报模板列表失败：{short_error(last_error) if last_error else '未知错误'}")
    data = parse_json_response(response, "读取日报模板列表")
    templates = data.get("data") or []
    if not isinstance(templates, list):
        raise RuntimeError("读取日报模板列表返回格式不正确。")
    for template in templates:
        if str(template.get("name") or "").strip() == template_name and template.get("id"):
            return str(template["id"])
    available = "、".join(str(item.get("name") or "") for item in templates if "广东" in str(item.get("name") or ""))[:300]
    raise RuntimeError(f"日报模板里没有找到“{template_name}”。可见广东模板：{available or '无'}")


def daily_report_exists(context, report_name: str, report_date: str) -> bool:
    response = context.request.get(
        f"{BASE_URL}/huaneng/group/api/report",
        params={
            "startDate": report_date,
            "endDate": report_date,
            "keyword": report_name,
            "numPerPage": 10,
            "pageNum": 1,
        },
        timeout=REPORT_API_TIMEOUT_MS,
    )
    data = parse_json_response(response, "查询已有集团日报")
    payload = data.get("data") or {}
    rows = payload.get("datas") or []
    if not isinstance(rows, list):
        return False
    return any(str(row.get("reportName") or "") == report_name for row in rows)


def create_daily_report(context, run_date: str, *, headless: bool) -> None:
    report_date = validate_date(run_date)
    report_name = daily_report_name(report_date)
    page = open_day_report_page(context, allow_manual=not headless)
    log(f"正在新建集团日报：{report_name}")
    if daily_report_exists(context, report_name, report_date):
        log(f"集团日报已存在，跳过新建：{report_name}")
        return
    template_id = find_daily_report_template_id(context)
    response = context.request.post(
        f"{BASE_URL}/huaneng/group/api/report",
        data={
            "reportName": report_name,
            "startDate": report_date,
            "endDate": report_date,
            "templateId": template_id,
            "provinceId": None,
        },
        timeout=120000,
    )
    parse_json_response(response, "新建集团日报")
    log("集团日报新建操作已提交。")


def dump_page_diagnostics(page: Page, prefix: str) -> str:
    safe_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", prefix).strip("_") or "page"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_path = DIAGNOSTICS_DIR / f"{safe_prefix}_{stamp}"
    data: dict[str, Any] = {"url": page.url, "title": "", "bodyText": ""}
    try:
        data["title"] = page.title()
    except PlaywrightError:
        pass
    try:
        data["bodyText"] = body_text(page)[:3000]
    except PlaywrightError:
        pass
    json_path = base_path.with_suffix(".json")
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        page.screenshot(path=str(base_path.with_suffix(".png")), full_page=True, timeout=10000)
    except PlaywrightError:
        pass
    return str(json_path)


def current_date_inputs(page: Page) -> list[dict[str, Any]]:
    return page.evaluate(
        """
        () => {
          const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
          const compact = s => String(s || '').replace(/\\s+/g, ' ').trim();
          return [...document.querySelectorAll('input')].map((el, index) => ({
            index,
            type: el.getAttribute('type') || '',
            value: compact(el.value),
            placeholder: compact(el.getAttribute('placeholder')),
            aria: compact(el.getAttribute('aria-label')),
            className: compact(el.className),
            visible: visible(el),
            score: (
              /date|picker|日期|时间/.test(`${el.type} ${el.className} ${el.placeholder} ${el.getAttribute('aria-label') || ''}`) ? 10 : 0
            ) + (/^\\d{4}[-/]\\d{1,2}[-/]\\d{1,2}$/.test(el.value) ? 20 : 0)
          })).filter(item => item.visible).sort((a, b) => b.score - a.score);
        }
        """
    )


def set_report_date(page: Page, target_date: str) -> None:
    inputs = current_date_inputs(page)
    candidates = [
        item
        for item in inputs
        if item["score"] > 0
        or "日期" in item["placeholder"]
        or "时间" in item["placeholder"]
        or "date" in item["type"].lower()
    ]
    if not candidates:
        raise RuntimeError("没有找到网页日期输入框。")

    chosen = candidates[0]
    current = str(chosen.get("value") or "").replace("/", "-")
    if current == target_date:
        log(f"网页日期已是 {target_date}。")
        return

    log(f"正在把网页日期从“{current or '空'}”调整为 {target_date}。")
    locator = page.locator("input").nth(int(chosen["index"]))
    try:
        locator.click(timeout=8000)
        locator.press("Control+A", timeout=5000)
        locator.fill(target_date, timeout=8000)
        locator.press("Enter", timeout=5000)
        page.wait_for_timeout(800)
    except PlaywrightError:
        page.evaluate(
            """
            ({ index, value }) => {
              const input = document.querySelectorAll('input')[index];
              if (!input) throw new Error('date input not found');
              const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
              setter.call(input, value);
              input.dispatchEvent(new Event('input', { bubbles: true }));
              input.dispatchEvent(new Event('change', { bubbles: true }));
              input.blur();
            }
            """,
            {"index": int(chosen["index"]), "value": target_date},
        )
        page.wait_for_timeout(800)

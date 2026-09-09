"""새 공고를 이메일로 알린다 (표준 라이브러리 smtplib)."""

from __future__ import annotations

import html as html_mod
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate


class NotifyError(Exception):
    """메일 발송 실패."""


def resolve_password(email_cfg: dict) -> str:
    """설정에 적어 둔 비밀번호, 없으면 환경변수에서 읽는다."""
    password = (email_cfg.get("password") or "").strip()
    if password:
        return password
    env_name = (email_cfg.get("password_env") or "").strip()
    return os.environ.get(env_name, "") if env_name else ""


BODY_STYLE = ("<div style=\"font-family:'Malgun Gothic',AppleSDGothicNeo-Regular,sans-serif;"
              "font-size:14px;color:#1f2328;line-height:1.6\">")
FOOTER = ("<p style='margin-top:24px;color:#656d76;font-size:12px'>"
          "채용공고 모니터가 자동으로 보낸 메일입니다.</p></div>")


def _period(since: str) -> str:
    """'9월 8일 08:00 이후' 처럼, 모아 둔 기간을 알려 주는 한 줄."""
    if not since:
        return ""
    try:
        moment = datetime.fromisoformat(since).astimezone()
    except (TypeError, ValueError):
        return ""
    return f"{moment.strftime('%m월 %d일 %H:%M')} 이후 발견한 공고입니다."


def render(results: list, subject_prefix: str = "[채용 알림]", since: str = "",
           page_url: str = ""):
    """새 공고 알림 메일 (공고를 받아 볼 사람들에게 가는 메일).

    사이트 점검 안내나 접속 실패 같은 운영 이야기는 여기 넣지 않는다.
    그건 render_alerts() 로 따로 만들어 관리자에게만 보낸다.
    """
    with_new = [r for r in results if r.get("new_items")]
    total = sum(len(r["new_items"]) for r in with_new)
    if len(with_new) == 1:
        subject = f"{subject_prefix} {with_new[0]['site_name']} 새 공고 {total}건"
    else:
        subject = f"{subject_prefix} 새 공고 {total}건 ({len(with_new)}개 사이트)"

    text_lines = []
    html_parts = [BODY_STYLE]
    if total:
        html_parts.append(f"<h2 style='margin:0 0 16px'>새로 올라온 공고 {total}건</h2>")
    period = _period(since)
    if period:
        text_lines.append(period)
        text_lines.append("")
        html_parts.append(f"<p style='margin:-8px 0 16px;color:#656d76'>{html_mod.escape(period)}</p>")
    for result in with_new:
        link = result.get("site_link") or result["site_url"]
        text_lines.append(f"[{result['site_name']}] {link}")
        html_parts.append(
            f"<h3 style='margin:20px 0 8px'>{html_mod.escape(result['site_name'])}"
            f" <a href='{html_mod.escape(link)}' "
            "style='font-size:12px;font-weight:400;color:#0969da'>사이트 열기</a></h3><ul style='padding-left:18px'>"
        )
        for item in result["new_items"]:
            title = item.get("title") or "(제목 없음)"
            url = item.get("url") or link
            date = item.get("date") or ""
            text_lines.append(f"  - {title}{(' (' + date + ')') if date else ''}\n    {url}")
            html_parts.append(
                f"<li style='margin-bottom:6px'><a href='{html_mod.escape(url)}' "
                f"style='color:#0969da;text-decoration:none'>{html_mod.escape(title)}</a>"
                + (f" <span style='color:#656d76'>{html_mod.escape(date)}</span>" if date else "")
                + "</li>"
            )
        html_parts.append("</ul>")
        text_lines.append("")

    if page_url:
        text_lines.append("")
        text_lines.append(f"지난 공고까지 모아 보기: {page_url}")
        html_parts.append(
            f"<p style='margin:24px 0 0'><a href='{html_mod.escape(page_url)}' "
            "style='color:#0969da'>공고 이력 페이지에서 보기</a>"
            " <span style='color:#656d76'>— 사내망에서 링크가 열리지 않을 때, "
            "여기서 주소를 복사해 개인 브라우저에 붙여넣으세요.</span></p>")
    html_parts.append(FOOTER)
    return subject, "\n".join(text_lines).strip() + "\n", "".join(html_parts)


def render_alerts(alerts: list, results: list = None, subject_prefix: str = "[채용 알림]"):
    """사이트 점검 안내 메일 (관리자에게만 가는 메일).

    감시가 조용히 망가진 정황과, 이번에 접속하지 못한 사이트를 담는다.
    """
    alerts = alerts or []
    results = results or []
    problems = [a for a in alerts if not a.get("recovered")]
    if problems:
        subject = f"{subject_prefix} 사이트 점검 필요 {len(problems)}건"
    else:
        subject = f"{subject_prefix} 사이트 점검 안내 (정상 복구 {len(alerts)}건)"

    text_lines = []
    html_parts = [BODY_STYLE]
    html_parts.append("<h2 style='margin:0 0 16px'>사이트 점검 안내</h2>")
    html_parts.append("<ul style='padding-left:18px'>")
    for alert in alerts:
        mark = "정상 복구" if alert.get("recovered") else "점검 필요"
        text_lines.append(f"  - [{mark}] {alert['site_name']}: {alert['label']} — {alert['detail']}")
        text_lines.append(f"    {alert.get('site_link') or alert.get('site_url', '')}")
        color = "#1a7f37" if alert.get("recovered") else "#bc4c00"
        html_parts.append(
            f"<li style='margin-bottom:6px'><b style='color:{color}'>[{mark}]</b> "
            f"<a href='{html_mod.escape(alert.get('site_link') or alert.get('site_url', ''))}' "
            f"style='color:#0969da;text-decoration:none'>{html_mod.escape(alert['site_name'])}</a> — "
            f"{html_mod.escape(alert['label'])}<br />"
            f"<span style='color:#656d76'>{html_mod.escape(alert['detail'])}</span></li>"
        )
    html_parts.append("</ul>")

    errors = [r for r in results if r.get("status") == "error"]
    if errors:
        text_lines.append("")
        text_lines.append("확인하지 못한 사이트:")
        html_parts.append("<h3 style='margin:20px 0 8px;color:#bc4c00'>확인하지 못한 사이트</h3>"
                          "<ul style='padding-left:18px'>")
        for result in errors:
            text_lines.append(f"  - {result['site_name']}: {result.get('error', '')}")
            html_parts.append(
                f"<li>{html_mod.escape(result['site_name'])}: "
                f"{html_mod.escape(result.get('error', ''))}</li>"
            )
        html_parts.append("</ul>")

    html_parts.append(FOOTER)
    return subject, "\n".join(text_lines).strip() + "\n", "".join(html_parts)


def send(email_cfg: dict, recipients: list, subject: str, text_body: str, html_body: str = "") -> None:
    """SMTP 로 메일을 보낸다. 실패하면 NotifyError."""
    recipients = [addr for addr in (recipients or []) if addr]
    if not recipients:
        raise NotifyError("수신 이메일이 비어 있습니다.")
    host = (email_cfg.get("smtp_host") or "").strip()
    if not host:
        raise NotifyError("SMTP 서버 주소가 비어 있습니다.")
    port = int(email_cfg.get("smtp_port") or 587)
    security = email_cfg.get("security") or "starttls"
    username = (email_cfg.get("username") or "").strip()
    password = resolve_password(email_cfg)
    sender = (email_cfg.get("from_addr") or username or recipients[0]).strip()

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=True)
    message.set_content(text_body or "")
    if html_body:
        message.add_alternative(html_body, subtype="html")

    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=30)
        with server:
            server.ehlo()
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if username and password:
                server.login(username, password)
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise NotifyError(
            "SMTP 로그인 실패입니다. 아이디/비밀번호를 확인하세요. "
            "(Gmail 은 2단계 인증 후 만든 '앱 비밀번호'를 써야 합니다.) "
            f"원문: {exc.smtp_error.decode('utf-8', 'replace') if isinstance(exc.smtp_error, bytes) else exc}"
        ) from exc
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise NotifyError(f"메일 발송 실패: {exc}") from exc


def send_test(email_cfg: dict, recipients: list) -> None:
    subject = f"{email_cfg.get('subject_prefix') or '[채용 알림]'} 테스트 메일"
    send(
        email_cfg,
        recipients,
        subject,
        "채용공고 모니터 테스트 메일입니다. 이 메일이 보이면 설정이 정상입니다.\n",
        "<p>채용공고 모니터 <b>테스트 메일</b>입니다. 이 메일이 보이면 설정이 정상입니다.</p>",
    )

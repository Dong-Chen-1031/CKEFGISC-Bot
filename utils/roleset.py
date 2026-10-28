"""身分組集合：運算式解析、求值，以及衍伸身分組的資料表

運算式把身分組當成「成員的集合」來運算，例如 `(@幹部 | @顧問) - @畢業生`；
也可以直接寫個人，當成只有一個人的集合，例如 `@幹部 | @小明`。
全集是伺服器裡所有成員（包含機器人），補集也以此為準；
`bot` 與 `human` 分別代表機器人與真人，要排除機器人就寫 `~@已驗證 - bot`。

資料庫裡存的是以 ID 表示的標準形式（`<@&123> | <@&456>`），
身分組改名不會讓運算式失效；同一個字串直接丟進嵌入就會顯示成身分組提及。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlmodel import Field, SQLModel, select

from utils.timeparse import now

# ---------------------------------------------------------------- 語法

# 同義的運算子都先統一成 ASCII 版本
OPERATORS = {
    "~": "~", "!": "~", "¬": "~",
    "&": "&", "∩": "&",
    "|": "|", "+": "|", "∪": "|",
    "-": "-", "∖": "-", "\\": "-",
    "^": "^", "△": "^",
    "(": "(", ")": ")",
    "*": "*",
}
# 裸名稱遇到這些字元就結束；要用到的話請把名稱用引號包起來
STOP_CHARS = set(OPERATORS) | {'"', "「", "」", "<", ">"}
QUOTES = {'"': '"', "「": "」"}
ROLE_MENTION = re.compile(r"<@&(\d+)>")
USER_MENTION = re.compile(r"<@!?(\d+)>")
# 不是身分組、但可以當成集合用的關鍵字（不分大小寫，加引號就會當成身分組名稱）
KEYWORDS = {
    "*": ("everyone", "@everyone", "全體"),
    "bot": ("bot", "bots", "@bot", "機器人"),
    "human": ("human", "humans", "@human", "真人"),
}
KEYWORD_OF = {alias: key for key, aliases in KEYWORDS.items() for alias in aliases}
# 機器人成員的身分組集合裡會多放這個假 ID，bot / human 就能跟身分組一樣判斷
BOT_MARKER = -1
# Discord Markdown 會吃掉的字元：* 清單／斜體、- 清單、~ 刪除線、| 劇透、_ 底線、\ 跳脫本身
MD_SPECIAL = re.compile(r"([\\*\-~|_])")


class ExprError(ValueError):
    """運算式寫錯了，訊息可以直接拿給使用者看"""


@dataclass(frozen=True)
class Token:
    kind: str  # 運算子本身、"role"（ID，可能是身分組或成員）、"user"（成員 ID）或 "name"（名稱）
    value: str | int | None
    pos: int  # 從 1 開始算，給錯誤訊息用
    quoted: bool = False  # 加了引號的名稱不當關鍵字


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
            continue

        mention = ROLE_MENTION.match(text, i)
        if mention:
            tokens.append(Token("role", int(mention.group(1)), i + 1))
            i = mention.end()
            continue

        mention = USER_MENTION.match(text, i)
        if mention:
            tokens.append(Token("user", int(mention.group(1)), i + 1))
            i = mention.end()
            continue

        if ch in OPERATORS:
            tokens.append(Token(OPERATORS[ch], None, i + 1))
            i += 1
            continue

        if ch in QUOTES:
            end = text.find(QUOTES[ch], i + 1)
            if end == -1:
                raise ExprError(f"第 {i + 1} 個字開始的引號沒有結尾")
            name = text[i + 1 : end]
            if not name:
                raise ExprError(f"第 {i + 1} 個字是空的名稱")
            tokens.append(Token("name", name, i + 1, quoted=True))
            i = end + 1
            continue

        if ch in STOP_CHARS:
            raise ExprError(f"第 {i + 1} 個字的 `{ch}` 看不懂")

        start = i
        while i < len(text) and not text[i].isspace() and text[i] not in STOP_CHARS:
            i += 1
        word = text[start:i]
        if word.isdigit():
            tokens.append(Token("role", int(word), start + 1))
        else:
            tokens.append(Token("name", word, start + 1))
    return tokens


# ---------------------------------------------------------------- 語法樹

# 優先序：數字越大綁得越緊，canonical() 靠它決定要不要加括號
PREC_BINARY = 1
PREC_AND = 2
PREC_ATOM = 3


class Node:
    prec = PREC_ATOM

    def matches(self, role_ids: set[int]) -> bool:
        """擁有這些身分組的成員是否落在集合裡"""
        raise NotImplementedError

    def roles(self) -> set[int]:
        """運算式引用到的身分組"""
        raise NotImplementedError

    def canonical(self) -> str:
        """以 ID 表示的標準形式，可以再丟回 parse() 解析"""
        raise NotImplementedError

    def wrap(self, child: Node, strict: bool) -> str:
        text = child.canonical()
        if child.prec < self.prec or (strict and child.prec == self.prec):
            return f"({text})"
        return text

    def display(self) -> str:
        """給人看的版本，可以安全放進 Discord 訊息"""
        return display(self.canonical())

    def __str__(self) -> str:
        return self.canonical()


@dataclass(frozen=True)
class All(Node):
    def matches(self, role_ids):
        return True

    def roles(self):
        return set()

    def canonical(self):
        return "*"


@dataclass(frozen=True)
class Bots(Node):
    def matches(self, role_ids):
        return BOT_MARKER in role_ids

    def roles(self):
        return set()

    def canonical(self):
        return "bot"


@dataclass(frozen=True)
class Humans(Node):
    def matches(self, role_ids):
        return BOT_MARKER not in role_ids

    def roles(self):
        return set()

    def canonical(self):
        return "human"


@dataclass(frozen=True)
class UserRef(Node):
    """單一成員，當成只有一個人的集合

    成員 ID 跟身分組 ID 都是 Discord 的 snowflake，不會撞號，
    所以直接放進同一個集合裡判斷（見 member_roles）。
    """

    user_id: int

    def matches(self, role_ids):
        return self.user_id in role_ids

    def roles(self):
        return set()

    def canonical(self):
        return f"<@{self.user_id}>"


@dataclass(frozen=True)
class RoleRef(Node):
    role_id: int

    def matches(self, role_ids):
        return self.role_id in role_ids

    def roles(self):
        return {self.role_id}

    def canonical(self):
        return f"<@&{self.role_id}>"


@dataclass(frozen=True)
class Not(Node):
    operand: Node

    def matches(self, role_ids):
        return not self.operand.matches(role_ids)

    def roles(self):
        return self.operand.roles()

    def canonical(self):
        return "~" + self.wrap(self.operand, strict=False)


@dataclass(frozen=True)
class Binary(Node):
    op: str
    left: Node
    right: Node

    @property
    def prec(self):
        return PREC_AND if self.op == "&" else PREC_BINARY

    def matches(self, role_ids):
        a = self.left.matches(role_ids)
        b = self.right.matches(role_ids)
        if self.op == "&":
            return a and b
        if self.op == "|":
            return a or b
        if self.op == "-":
            return a and not b
        return a != b  # "^" 對稱差

    def roles(self):
        return self.left.roles() | self.right.roles()

    def canonical(self):
        # 左結合，所以右邊同優先序的要加括號：A - (B | C) 不等於 A - B | C。
        # 左邊不加也對，但 A | B - C 很容易被看成 A | (B - C)，不同運算子混用時還是加上
        mixed = isinstance(self.left, Binary) and self.left.op != self.op
        left = self.wrap(self.left, strict=mixed)
        right = self.wrap(self.right, strict=True)
        return f"{left} {self.op} {right}"


# ---------------------------------------------------------------- 解析


class Parser:
    """遞迴下降

    expr    := inter (("|" | "-" | "^") inter)*
    inter   := unary ("&" unary)*
    unary   := "~" unary | primary
    primary := "(" expr ")" | "*" | 身分組
    """

    def __init__(self, text: str, guild=None):
        self.text = text
        self.guild = guild
        self.tokens = tokenize(text)
        self.i = 0

    def peek(self) -> Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> Token:
        token = self.tokens[self.i]
        self.i += 1
        return token

    def parse(self) -> Node:
        if not self.tokens:
            raise ExprError("運算式是空的")
        node = self.expr()
        token = self.peek()
        if token is not None:
            if token.kind == ")":
                raise ExprError(f"第 {token.pos} 個字多了一個右括號")
            raise ExprError(f"第 {token.pos} 個字前面少了運算子（`|`、`&`、`-`…）")
        return node

    def expr(self) -> Node:
        node = self.inter()
        while (token := self.peek()) and token.kind in ("|", "-", "^"):
            self.take()
            node = Binary(token.kind, node, self.inter())
        return node

    def inter(self) -> Node:
        node = self.unary()
        while (token := self.peek()) and token.kind == "&":
            self.take()
            node = Binary("&", node, self.unary())
        return node

    def unary(self) -> Node:
        token = self.peek()
        if token is not None and token.kind == "~":
            self.take()
            return Not(self.unary())
        return self.primary()

    def primary(self) -> Node:
        token = self.peek()
        if token is None:
            raise ExprError("運算式在結尾少了一個身分組")
        self.take()

        if token.kind == "(":
            node = self.expr()
            close = self.peek()
            if close is None or close.kind != ")":
                raise ExprError(f"第 {token.pos} 個字的左括號沒有對應的右括號")
            self.take()
            return node
        if token.kind == "*":
            return All()
        if token.kind == "role":
            return self.role_by_id(token)
        if token.kind == "user":
            return self.user_by_id(token)
        if token.kind == "name":
            return self.role_by_name(token)
        raise ExprError(f"第 {token.pos} 個字的 `{token.kind}` 前面少了一個身分組")

    def role_by_id(self, token: Token) -> Node:
        role_id = token.value
        if self.guild is None:
            return RoleRef(role_id)
        # @everyone 的 ID 就是伺服器 ID
        if role_id == self.guild.id:
            return All()
        if self.guild.get_role(role_id) is not None:
            return RoleRef(role_id)
        if self.guild.get_member(role_id) is not None:
            return UserRef(role_id)
        # 純數字也可能是名稱
        return self.role_by_name(Token("name", str(role_id), token.pos))

    def user_by_id(self, token: Token) -> Node:
        if self.guild is not None and self.guild.get_member(token.value) is None:
            raise ExprError(f"第 {token.pos} 個字：<@{token.value}> 不在這個伺服器裡")
        return UserRef(token.value)

    def role_by_name(self, token: Token) -> Node:
        name: str = token.value
        keyword = None if token.quoted else KEYWORD_OF.get(name.lower())
        if keyword is not None:
            return {"*": All, "bot": Bots, "human": Humans}[keyword]()
        if self.guild is None:
            raise ExprError(f"第 {token.pos} 個字：沒有伺服器資訊，只能用 ID")

        candidates = [name]
        if name.startswith("@") and len(name) > 1:
            candidates.append(name[1:])  # 習慣打 @幹部 的寫法

        roles = [r for r in self.guild.roles if not r.is_default()]
        for wanted in candidates:
            found = [r for r in roles if r.name == wanted]
            if not found:
                found = [r for r in roles if r.name.lower() == wanted.lower()]
            if len(found) == 1:
                return RoleRef(found[0].id)
            if len(found) > 1:
                raise ExprError(f"有 {len(found)} 個身分組都叫「{wanted}」，請改用提及或 ID")

        # 沒有這個名稱的身分組，再找成員（帳號名稱、顯示名稱、伺服器暱稱都算）
        for wanted in candidates:
            lowered = wanted.lower()
            found = [
                m
                for m in self.guild.members
                if lowered in {(n or "").lower() for n in (m.name, m.global_name, m.nick)}
            ]
            if len(found) == 1:
                return UserRef(found[0].id)
            if len(found) > 1:
                raise ExprError(f"有 {len(found)} 位成員都叫「{wanted}」，請改用提及或 ID")
        raise ExprError(f"第 {token.pos} 個字：找不到叫「{name}」的身分組或成員")


def parse(text: str, guild=None) -> Node:
    """解析運算式

    給 guild 的話會把名稱換成 ID、並確認身分組存在；
    不給的話只接受 ID / 提及（讀資料庫裡的標準形式時用）。
    """
    return Parser(text, guild).parse()


def display(expression: str) -> str:
    """把標準形式轉成能安全放進 Discord 訊息的樣子

    `* - <@&1>` 直接送出去會在行首被當成兩層清單、`~~<@&1>` 會變刪除線，
    所以運算子都加上反斜線；身分組提及保持原樣，才會繼續顯示成身分組。
    """
    parts = []
    last = 0
    for mention in ROLE_MENTION.finditer(expression):
        parts.append(MD_SPECIAL.sub(r"\\\1", expression[last : mention.start()]))
        parts.append(mention.group(0))
        last = mention.end()
    parts.append(MD_SPECIAL.sub(r"\\\1", expression[last:]))
    return "".join(parts)


def name_token(name: str) -> str:
    """把身分組名稱寫成運算式裡能用的樣子，必要時加引號"""
    if name and not name.isdigit() and name.lower() not in KEYWORD_OF and all(
        not c.isspace() and c not in STOP_CHARS for c in name
    ):
        return name
    if '"' not in name:
        return f'"{name}"'
    return f"「{name}」"


# ---------------------------------------------------------------- 求值


def member_roles(member) -> set[int]:
    """成員的身分組 ID 加上自己的 ID（給 UserRef 判斷），機器人再多一個 BOT_MARKER"""
    role_ids = {r.id for r in member.roles}
    role_ids.add(member.id)
    if member.bot:
        role_ids.add(BOT_MARKER)
    return role_ids


def evaluate(node: Node, guild) -> list:
    """運算結果的成員清單"""
    return [m for m in guild.members if node.matches(member_roles(m))]


Rule = tuple[int, Node]  # (衍伸身分組 ID, 運算式)


def order_rules(rules: list[Rule]) -> list[Rule]:
    """依賴排序：被引用的衍伸身分組先算

    存檔前已經擋掉循環，理論上不會剩下；萬一資料庫被手動改出循環，
    剩下的照原順序排在最後，至少不會整個停擺。
    """
    pending = dict(rules)
    ordered: list[Rule] = []
    while pending:
        ready = [
            rid for rid, node in pending.items() if not (node.roles() & (pending.keys() - {rid}))
        ]
        if not ready:
            ordered.extend(pending.items())
            break
        for rid in ready:
            ordered.append((rid, pending.pop(rid)))
    return ordered


def apply_rules(role_ids: set[int], rules: list[Rule]) -> set[int]:
    """依序套用（已排序的）規則，回傳成員應該要有的身分組

    逐條更新模擬中的身分組集合，所以衍伸套衍伸一次就算到底。
    """
    current = set(role_ids)
    for role_id, node in rules:
        if node.matches(current):
            current.add(role_id)
        else:
            current.discard(role_id)
    return current


def find_cycle(role_id: int, node: Node, graph: dict[int, set[int]]) -> list[int] | None:
    """把 role_id 設成 node 之後會不會形成循環，會的話回傳循環路徑

    graph 是現有的「衍伸身分組 -> 引用的身分組」。
    """
    graph = {**graph, role_id: node.roles()}
    path: list[int] = []

    def visit(current: int) -> bool:
        path.append(current)
        for dep in graph.get(current, ()):
            if dep == role_id:
                path.append(dep)
                return True
            if dep in graph and dep not in path and visit(dep):
                return True
        path.pop()
        return False

    return path if visit(role_id) else None


def chunk_mentions(
    header: str, user_ids: list[int], limit: int = 2000
) -> list[tuple[str, list[int]]]:
    """把一串使用者提及切成不超過 limit 字的訊息，第一則帶 header

    回傳 (訊息, 這則裡列出的使用者)。送出時只放行這些人的提及 ——
    header 裡的運算式可能寫了 `@高三 - @小華`，不能反而 ping 到被排除的小華。
    一則 2000 字最多塞得下約 95 個提及，不會超過 Discord 每則 100 人的白名單上限。
    """
    if len(header) > limit:
        header = header[: limit - 1] + "…"
    messages: list[tuple[str, list[int]]] = []
    current, ids = header, []
    for uid in user_ids:
        mention = f"<@{uid}>"
        sep = "\n" if current == header and header else " "
        if current and len(current) + len(sep) + len(mention) > limit:
            messages.append((current, ids))
            current, ids = mention, [uid]
        else:
            current = f"{current}{sep}{mention}" if current else mention
            ids.append(uid)
    if current:
        messages.append((current, ids))
    return messages


# ---------------------------------------------------------------- 資料表


class DerivedRole(SQLModel, table=True):
    """一個身分組綁一條運算式，機器人負責讓它的成員等於運算結果"""

    __tablename__ = "derived_role"

    role_id: int = Field(primary_key=True)
    guild_id: int = Field(index=True)
    # 標準形式，以 ID 表示
    expression: str
    # 引用的身分組被刪掉時會自動停用，重新設定運算式就會恢復
    enabled: bool = True
    created_by: int | None = None
    updated_at: str = Field(default_factory=lambda: now().isoformat())

    @property
    def node(self) -> Node:
        return parse(self.expression)


def get_derived(role_id: int) -> DerivedRole | None:
    from utils.db import session

    with session() as s:
        return s.get(DerivedRole, role_id)


def guild_derived(guild_id: int) -> list[DerivedRole]:
    from utils.db import session

    with session() as s:
        return list(s.exec(select(DerivedRole).where(DerivedRole.guild_id == guild_id)).all())


def all_guild_ids() -> list[int]:
    from utils.db import session

    with session() as s:
        return sorted(set(s.exec(select(DerivedRole.guild_id)).all()))


def save_derived(row: DerivedRole) -> DerivedRole:
    """新增或更新（role_id 是主鍵，merge 會自動判斷）"""
    from utils.db import session

    row.updated_at = now().isoformat()
    with session() as s:
        merged = s.merge(row)
        s.commit()
        return merged


def delete_derived(role_id: int) -> bool:
    """回傳 True 代表本來有這筆、已經刪掉"""
    from utils.db import session

    with session() as s:
        row = s.get(DerivedRole, role_id)
        if row is None:
            return False
        s.delete(row)
        s.commit()
        return True


def dependency_graph(guild_id: int, exclude: int | None = None) -> dict[int, set[int]]:
    """這個伺服器所有衍伸身分組（含停用中的）引用了誰"""
    return {
        row.role_id: row.node.roles()
        for row in guild_derived(guild_id)
        if row.role_id != exclude
    }


def load_rules(guild_id: int) -> list[Rule]:
    """啟用中的規則，已依賴排序"""
    rules = []
    for row in guild_derived(guild_id):
        if not row.enabled:
            continue
        try:
            rules.append((row.role_id, row.node))
        except ExprError:
            continue  # 標準形式不該解析失敗，真的壞了就跳過這條
    return order_rules(rules)

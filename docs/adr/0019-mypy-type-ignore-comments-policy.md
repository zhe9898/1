# ADR 0019: Mypy Type Ignore Comments Policy (Strict Analysis)

## 背景

在对 ZEN70 并发执行严苛的全栈 `mypy` 类型检查清理过程中（从 415 个错误降至 0 个），暴露出一个高隐蔽性的类型系统逃逸漏洞与误报陷阱：**双重注释遮挡（Double Comment Masking）**。

当在存在类型检查错误的行添加忽略指令时，如果在一行内同时包含常规业务注释（特别是中文注释）和 `# type: ignore[xxx]`，`mypy` 会因为解析器的贪婪匹配或截断，导致 `type: ignore` 失去抑制作用。

例如，以下代码会导致 `mypy` 持续抛出错误：
```python
is_new = await self.redis.set(idx_key, "1", nx=True, ex=300)  # 5分钟防重放  # type: ignore[union-attr]
```

## 决策

为了确保法典规定的“全栈技术路线红线（强类型强制）”与 CI/CD 流程中的 0 Error 门禁不被阻塞，现确立以下强制重构红线：

1. **类型抑制指令必须独占首位注释**：
   在一行代码中，如果必须使用 `# type: ignore`，该指令必须紧跟在一行代码语句之后，成为行内**第一个** `#` 注释内容。

   **合规（正确）示例**：
   ```python
   is_new = await self.redis.set(idx_key, "1", nx=True, ex=300)  # type: ignore[union-attr]  # 5分钟防重放
   ```

   **违规（禁止）示例**：
   ```python
   is_new = await self.redis.set(idx_key, "1", nx=True, ex=300)  # 5分钟防重放  # type: ignore[union-attr]
   ```

2. **多行函数签名的类型注解补齐规则**：
   在为主体较长、参数分行的函数（如 Pydantic 验证器、FastAPI 依赖）手工补齐 `-> None:` 或具体返回类型时，必须作用于控制流的**签名闭合行**，严禁错位添加至内部语句块或换行符断层处，防止引发 `no-untyped-def` 的无限死循环。

3. **双重/多重抑制码强制合并**：
   若某一行因为不同版本或不同基础库，`mypy` 报出多个不同的类型错误（例如同时报 `[arg-type]` 和 `[type-var]`），必须合并在一个中括号内，严禁在同一行写多个 `# type: ignore` 标签段：
   - ✅ 正确：`# type: ignore[arg-type, type-var]`
   - ❌ 错误：`# type: ignore[arg-type] # type: ignore[type-var]`

## 后果

- **优点**：
  - 消灭了 `mypy` 在 CI 工作流中因为解析干扰导致的“幽灵报错”，保障了流水线的确定性。
  - 标准化了静态类型抑制语法，使 `unused-ignore` 警告可以准确触发，避免无用的忽略掩盖未来的代码变动缺陷。
- **代价**：
  - 需要开发者在编写代码时额外注意注释的书写顺序。
  - 之前的存量代码若存在中文注释在前的写法，必须进行全量正向清洗（已在本次清零行动中完成）。

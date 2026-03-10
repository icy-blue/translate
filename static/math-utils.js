// ==========================================
// 数学公式处理工具函数集合
// ==========================================

const MathUtils = {
  // 1. 标准化 LaTeX 格式
  // 将各种格式的数学公式转换为标准格式：\[...\] 用于块公式，\(...\) 用于行内公式
  normalizeLatex(text) {
    const lines = text.split("\n");
    const out = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      // 处理跨多行块公式: [ ... ] 或 \[ ... \]
      if (
        trimmed === "[" ||
        trimmed === "\\[" ||
        (
          (trimmed.startsWith("[") || trimmed.startsWith("\\[")) &&
          !trimmed.includes(trimmed.startsWith("\\[") ? "\\]" : "]")
        )
      ) {
        const isEscaped = trimmed.startsWith("\\[");
        const closeToken = isEscaped ? "\\]" : "]";

        const formulaLines = [line];
        let j = i + 1;
        let found = false;

        while (j < lines.length) {
          formulaLines.push(lines[j]);
          if (lines[j].includes(closeToken)) {
            found = true;
            break;
          }
          j++;
        }

        if (!found) {
          out.push(...formulaLines);
          i = j;
          continue;
        }

        const full = formulaLines.join("\n");
        let startIdx, endIdx, formula;

        if (isEscaped) {
          startIdx = full.indexOf("\\[");
          endIdx = full.lastIndexOf("\\]");
          formula = full.slice(startIdx + 2, endIdx).trim();
        } else {
          startIdx = full.indexOf("[");
          endIdx = full.lastIndexOf("]");
          formula = full.slice(startIdx + 1, endIdx).trim();
        }

        // 只在看起来像数学公式时才转
        if (this.looksLikeMath(formula)) {
          out.push(`\\[\n${formula}\n\\]`);
        } else {
          out.push(...formulaLines);
        }

        i = j + 1;
        continue;
      }

      // 处理单行块公式: [ ... ] 或 \[ ... \]
      if (
        (trimmed.startsWith("[") && trimmed.endsWith("]")) ||
        (trimmed.startsWith("\\[") && trimmed.endsWith("\\]"))
      ) {
        let formula = "";

        if (trimmed.startsWith("\\[")) {
          formula = trimmed.slice(2, -2).trim();
        } else {
          formula = trimmed.slice(1, -1).trim();
        }

        if (this.looksLikeMath(formula)) {
          out.push(`\\[${formula}\\]`);
        } else {
          out.push(line);
        }

        i++;
        continue;
      }

      // 处理行内公式:
      // - 保留已有 \( ... \)
      // - 将疑似数学的裸 ( ... ) 转成 \( ... \)
      out.push(this.normalizeInlineMath(line));
      i++;
    }

    return out.join("\n");
  },

  // 2. 判断一段内容是否"像数学公式"
  looksLikeMath(formula) {
    if (!formula) return false;
    return /\\[a-zA-Z]+|[_^]|[=+\-*/]|\\frac|\\sum|\\int|\\sqrt|[A-Za-z]\([A-Za-z0-9]+\)|\{|\}|[0-9]/.test(formula);
  },

  // 3. 把行内的裸 ( ... ) 转成 \( ... \)，但尽量避免误伤普通文本
  normalizeInlineMath(line) {
    if (!line) return line;

    // 先保护已有的 \( ... \)
    const preserved = [];
    let text = line.replace(/\\\((?:\\.|[^\\])*?\\\)/g, (m) => {
      const token = `@@INLINE_LATEX_${preserved.length}@@`;
      preserved.push(m);
      return token;
    });

    // 再处理裸 ( ... )
    text = text.replace(/\(([^()\n]+)\)/g, (match, content) => {
      const formula = content.trim();
      if (this.looksLikeMath(formula)) {
        return `\\(${formula}\\)`;
      }
      return match;
    });

    // 还原已有的 \( ... \)
    text = text.replace(/@@INLINE_LATEX_(\d+)@@/g, (_, idx) => preserved[Number(idx)]);

    return text;
  },

  // 4. 保护数学块，避免 marked 解析其中内容
  protectMath(text) {
    const mathStore = [];
    let protectedText = text;

    // 先保护块公式 \[ ... \]
    protectedText = protectedText.replace(/\\\[[\s\S]*?\\\]/g, (match) => {
      const token = `@@MATH_BLOCK_${mathStore.length}@@`;
      mathStore.push(match);
      return token;
    });

    // 再保护行内公式 \( ... \)
    protectedText = protectedText.replace(/\\\([\s\S]*?\\\)/g, (match) => {
      const token = `@@MATH_INLINE_${mathStore.length}@@`;
      mathStore.push(match);
      return token;
    });

    return { protectedText, mathStore };
  },

  // 5. 恢复数学块
  restoreMath(html, mathStore) {
    return html.replace(/@@MATH_(?:BLOCK|INLINE)_(\d+)@@/g, (_, index) => {
      return mathStore[Number(index)];
    });
  },

  // 6. 总渲染函数：将 Markdown 内容转换为 HTML，并处理数学公式
  renderMarkdownWithMath(content) {
    const normalized = this.normalizeLatex(content);
    const { protectedText, mathStore } = this.protectMath(normalized);
    const html = marked.parse(protectedText);
    return this.restoreMath(html, mathStore);
  }
};

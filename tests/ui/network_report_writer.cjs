/* 同步保存网络守卫证据：只重试本地原子替换，不重试任何业务请求。 */
'use strict';
const fs = require('node:fs');
const { randomUUID } = require('node:crypto');

const RETRY_DELAYS_MS = Object.freeze([10, 25, 50]);
const pause = new Int32Array(new SharedArrayBuffer(4));

/** 目录、只读文件和无法核实的文件状态不归入可短暂重试的替换边界。 */
function replaceableFile(file, mayBeMissing = false) {
  try {
    const info = fs.statSync(file);
    return info.isFile() && (info.mode & 0o200) !== 0;
  } catch (error) { return mayBeMissing && error.code === 'ENOENT'; }
}

/** EPERM 不证明具体占用原因；仅对 Windows 的本次 rename 候选限次等待。 */
function retryableRename(error, temporary, file) {
  return process.platform === 'win32' && ['EPERM', 'EBUSY'].includes(error?.code)
    && error.syscall === 'rename' && error.path === temporary && error.dest === file
    && replaceableFile(temporary) && replaceableFile(file, true);
}

/** 每个守卫独立持有失败状态；失败后禁止 exit 再写掩盖未成功落盘的原件。 */
function createNetworkReportWriter(file) {
  let failure;
  return value => {
    if (failure) throw failure;
    if (!file) return;
    let temporary, attempts = 0, waited = 0;
    try {
      const contents = JSON.stringify(value, null, 2) + '\n';
      // 同目录唯一文件防止失败原件被下一次写入覆盖；从不提前删除有效目标。
      temporary = `${file}.${process.pid}.${randomUUID()}.tmp`;
      fs.writeFileSync(temporary, contents, { flag: 'wx' });
      for (;;) {
        attempts++;
        try { fs.renameSync(temporary, file); return; }
        catch (error) {
          if (attempts > RETRY_DELAYS_MS.length || !retryableRename(error, temporary, file)) throw error;
          const delay = RETRY_DELAYS_MS[attempts - 1];
          Atomics.wait(pause, 0, 0, delay);
          waited += delay;
        }
      }
    } catch (error) {
      // 保留原错误码及临时文件定位；调用方仍以抛错停止，不将旧报告冒充本次成功。
      failure = error;
      error.reportTemporary = temporary;
      error.reportDestination = file;
      error.reportRenameAttempts = attempts;
      error.reportRetryDelayMs = waited;
      throw error;
    }
  };
}

module.exports = { createNetworkReportWriter };

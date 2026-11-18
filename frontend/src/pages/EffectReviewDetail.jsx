import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api'
import EffectResult from '../components/EffectResult'

export default function EffectReviewDetail() {
  const { id } = useParams()
  const [out, setOut] = useState(null)
  const [msg, setMsg] = useState(null)
  const [center, setCenter] = useState('')
  const [comment, setComment] = useState('')

  async function reload() {
    setOut(await api.get(`/api/effect-outputs/${id}`))
  }
  useEffect(() => {
    reload().catch((e) => setMsg({ type: 'bad', text: e.message }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  async function decide(action, detail = {}) {
    setMsg(null)
    try {
      const res = await api.post(`/api/effect-outputs/${id}/decision`, { action, detail, comment })
      setOut(res)
      setCenter('')
      if (res.status === 'approved') {
        setMsg({ type: 'ok', text: `已批准并发布（效应报告 #${res.published_report_id}）` })
      } else if (res.flag_count > 0) {
        setMsg({ type: 'bad', text: `仍有 ${res.flag_count} 处差分反推标记未解决，输出留在审查队列` })
      }
    } catch (e) {
      setMsg({ type: 'bad', text: e.message })
    }
  }

  if (!out) return <div className="panel">加载中…</div>
  const pending = out.status === 'pending_review'
  const payload = out.payload
  const included = payload.coverage?.included_centers || []
  const suppressed = payload.suppressed_centers || []

  return (
    <>
      <div className="panel">
        <h2>审查效应输出 #{out.id}：{out.title}</h2>
        {msg && <div className={`alert ${msg.type === 'bad' ? 'bad' : 'ok'}`}>{msg.text}</div>}
        <p className="small muted">
          效应运行 #{out.run_id} · k={out.k_threshold} · 状态 <b>{out.status}</b>
        </p>
        {out.flags.length > 0 && (
          <div className="alert bad">
            <b>未解决标记：</b>
            <ul style={{ margin: '4px 0', paddingLeft: 18 }}>
              {out.flags.map((f, i) => <li key={i}>{f.text}</li>)}
            </ul>
          </div>
        )}
        <EffectResult payload={payload} />
      </div>

      {pending && (
        <div className="panel">
          <h3>审查决定</h3>
          <label className="field">
            审查意见
            <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
          </label>

          <h3>方式一：抑制纳入中心（披露保护）</h3>
          <p className="small muted">
            将中心移入受保护类别（其诊断值从输出中移除）。若该中心仍在汇总估计内，
            会触发差分反推标记，需重新汇总或抑制汇总估计后才能批准。
          </p>
          <div className="row">
            <select value={center} onChange={(e) => setCenter(e.target.value)}>
              <option value="">选择中心</option>
              {included.filter((c) => !suppressed.includes(c)).map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <button className="secondary" disabled={!center}
              onClick={() => decide('suppress_center', { center })}>
              抑制所选中心
            </button>
          </div>

          <h3>方式二：重新汇总 / 抑制汇总估计</h3>
          <div className="row">
            <button className="secondary" onClick={() => decide('repool')}>
              在未抑制中心上重新汇总
            </button>
            <button className="secondary" disabled={payload.pooled_suppressed}
              onClick={() => decide('suppress_pooled')}>
              抑制汇总估计本身
            </button>
          </div>

          <h3>方式三：直接决定</h3>
          <div className="row">
            <button disabled={out.flags.length > 0} onClick={() => decide('approve')}>
              批准并发布
            </button>
            <button className="danger" onClick={() => decide('reject')}>拒绝交付</button>
          </div>
          {out.flags.length > 0 && (
            <p className="small muted">存在未解决差分反推标记时不能批准。</p>
          )}
        </div>
      )}

      <div className="panel">
        <h3>审查记录</h3>
        {out.decisions.length === 0 ? <p className="muted">暂无。</p> : (
          <table className="grid">
            <thead><tr><th>时间</th><th>审查员</th><th>动作</th><th>细节</th><th>意见</th></tr></thead>
            <tbody>
              {out.decisions.map((d) => (
                <tr key={d.id}>
                  <td className="small">{d.decided_at.slice(0, 19).replace('T', ' ')}</td>
                  <td>{d.reviewer}</td>
                  <td><span className="tag info">{d.action}</span></td>
                  <td className="mono small">{JSON.stringify(d.detail)}</td>
                  <td className="small">{d.comment || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}

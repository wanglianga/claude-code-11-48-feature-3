import { useEffect, useState } from 'react'
import { api } from '../api'
import ResultTable from '../components/ResultTable'
import EffectResult from '../components/EffectResult'

export default function Reports() {
  const [reports, setReports] = useState([])
  const [effectReports, setEffectReports] = useState([])
  const [detail, setDetail] = useState(null)
  const [verify, setVerify] = useState(null)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    Promise.all([api.get('/api/reports'), api.get('/api/effect-reports')])
      .then(([r, er]) => { setReports(r); setEffectReports(er) })
      .catch((e) => setMsg(e.message))
  }, [])

  async function open(id) {
    const [r, v] = await Promise.all([
      api.get(`/api/reports/${id}`),
      api.get(`/api/reports/${id}/verify`),
    ])
    setDetail({ kind: 'table', data: r })
    setVerify(v)
  }

  async function openEffect(id) {
    const [r, v] = await Promise.all([
      api.get(`/api/effect-reports/${id}`),
      api.get(`/api/effect-reports/${id}/verify`),
    ])
    setDetail({ kind: 'effect', data: r })
    setVerify(v)
  }

  return (
    <>
      <div className="panel">
        <h2>已发布报告 · 统计输出</h2>
        {msg && <div className="alert bad">{msg}</div>}
        {reports.length === 0 ? <p className="muted">暂无已发布报告。</p> : (
          <table className="grid">
            <thead><tr><th>ID</th><th>标题</th><th>运行</th><th>发布时间</th><th>内容哈希</th><th></th></tr></thead>
            <tbody>
              {reports.map((r) => (
                <tr key={r.id}>
                  <td className="mono">#{r.id}</td>
                  <td>{r.title}</td>
                  <td className="mono">#{r.run_id}</td>
                  <td className="small">{r.published_at?.slice(0, 19).replace('T', ' ')}</td>
                  <td className="mono small">{r.report_hash.slice(0, 16)}…</td>
                  <td><button className="link" onClick={() => open(r.id)}>查看</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <h2>已发布报告 · 跨中心效应汇总</h2>
        {effectReports.length === 0 ? <p className="muted">暂无已发布效应报告。</p> : (
          <table className="grid">
            <thead><tr><th>ID</th><th>标题</th><th>效应运行</th><th>汇总版本</th><th>发布时间</th><th>内容哈希</th><th></th></tr></thead>
            <tbody>
              {effectReports.map((r) => (
                <tr key={r.id}>
                  <td className="mono">#{r.id}</td>
                  <td>{r.title}</td>
                  <td className="mono">#{r.run_id}</td>
                  <td>{r.pool_version ? `v${r.pool_version}` : '—'}</td>
                  <td className="small">{r.published_at?.slice(0, 19).replace('T', ' ')}</td>
                  <td className="mono small">{r.report_hash.slice(0, 16)}…</td>
                  <td><button className="link" onClick={() => openEffect(r.id)}>查看</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {detail && (
        <div className="modal-mask" onClick={() => setDetail(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>报告 #{detail.data.id}：{detail.data.report.title}</h3>
            {verify && (
              <div className={`alert ${verify.intact ? 'ok' : 'bad'}`}>
                哈希校验：{verify.intact ? '一致，报告内容可复现' : '不一致！'}
                <div className="mono small">{verify.stored_hash}</div>
              </div>
            )}
            {detail.kind === 'table' ? (
              <>
                <h4>结果表</h4>
                <ResultTable table={detail.data.report.table} />
              </>
            ) : (
              <>
                <h4>效应汇总结果</h4>
                <EffectResult payload={detail.data.report.payload} />
              </>
            )}
            <h4>完整运行版本引用（provenance）</h4>
            <pre className="json">{JSON.stringify(detail.data.report.provenance, null, 2)}</pre>
            <button className="secondary" onClick={() => setDetail(null)}>关闭</button>
          </div>
        </div>
      )}
    </>
  )
}

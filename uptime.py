import asyncio
import logging
import ssl
import aiohttp
from uptime_kuma_api import UptimeKumaApi, MonitorType, AuthMethod, MonitorStatus, UptimeKumaException

# 忽略https证书验证警告
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s", filename="log.log")

# TLS证书和密钥读取
tlsCert = open("./cert.crt", "r").read()
tlsKey = open("./key.key", "r").read()
tlsCa = open("./ca.crt", "r").read()
group_id = 11  # 分组ID

# API连接
uptime_kuma_url = 'http://ip:3001/'  # 例如：http://127.0.0.1:3001/
username = 'xxx'
password = 'xxxxx'
api = UptimeKumaApi(uptime_kuma_url)
api.login(username, password)


def create_ssl_context():
    """
    创建 SSL 上下文对象，并加载证书
    """
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(certfile="./cert.crt", keyfile="./key.key", password=None)
    context.load_verify_locations(cafile="./ca.crt")
    return context


async def check_pod():
    """
    查询 pod 并监控其状态
    # 该 url 为 k3s 容器的 namespace （gzctf-challenges）下的 pod 的信息
    # 获取 k3s的容器的 namespace：  （通过接口获取）
    # curl -ks --cert cert.crt --key key.key https://ip:6443/api/v1/namespaces/ | jq .items[].metadata.name
    """
    pod_message = {}  # {pod_name: creationTimestamp}
    url = "https://ip:6443/api/v1/namespaces/<your_namespace>/pods"

    # 创建 SSL 上下文
    ssl_context = create_ssl_context()

    # 使用 aiohttp 发送请求
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, ssl=ssl_context) as response:
                req_json = await response.json()
                # Ensure that response has the 'items' key
                if 'items' in req_json:
                    for item in req_json['items']:
                        pod_message[item['metadata']['name']] = item['metadata']['creationTimestamp']
                    logging.info(f"K3s Active container: {pod_message}")

                    # 判断 pod 是否在运行
                    for pod_name, creat_time in pod_message.items():
                        url = f"https://ip:6443/api/v1/namespaces/<your_namespace>/pods/{pod_name}"
                        async with session.get(url, ssl=ssl_context) as response:
                            req_json = await response.json()
                            if req_json['status']['phase'] == 'Running':
                                logging.info(f"{pod_name} is running, creationTimestamp: {creat_time}")
                                await auto_add_monitor(pod_name, creat_time)
                            else:
                                logging.info(f"{pod_name} is not running")
                else:
                    logging.error(f"Error: Response does not contain 'items'. Response: {req_json}")
        except Exception as e:
            logging.error(f"Error while checking pods: {e}")


async def auto_add_monitor(pod_name, pod_creation_time):
    """
    自动添加监控
    """
    monitored_name = await get_monitor_name()
    new_pod_name = pod_name.rsplit('-', 1)[0]
    new_monitor_name = new_pod_name + '-' + pod_creation_time
    logging.info(f'**********{new_pod_name + "-" + pod_creation_time} and {monitored_name} ************')
    if new_pod_name + '-' + pod_creation_time not in monitored_name:
        monotor_url = f"https://ip:6443/api/v1/namespaces/<your_namespcace>/pods/{pod_name}"
        # 采用mTLS认证，需要传入证书和密钥, 忽略证书验证(自签名证书)
        # Json 查询模式，查询是否 Running
        res = api.add_monitor(type=MonitorType.JSON_QUERY, jsonPath='status.phase',
                              expectedValue="Running", name=f"{new_pod_name}-{pod_creation_time}",
                              url=monotor_url,
                              authMethod=AuthMethod.MTLS, tlsCert=tlsCert, tlsKey=tlsKey, tlsCa=tlsCa,
                              ignoreTls=True,
                              parent=group_id,
                              timeout=30)
        logging.info(res)
        new_id = res['monitorID']  # 新增的监控项的ID， res={'msg': 'Added Successfully.', 'monitorID': 38}
        await edit_status_page(new_id)  # 修改状态页面 （基于save_status_page修改）
    else:
        logging.info(f"{new_pod_name}-{pod_creation_time} 已经在网站上被监测了")


async def delete_monitor():
    """
    删除状态为 DOWN 的监控项

    """
    monitor_list = api.get_monitors()  # No await here, as it's a synchronous function
    all_id = [i['id'] for i in monitor_list]
    logging.info(f"All monitor id=> {all_id}")
    for id in all_id:
        logging.info(f"现在查看id为{id}的页面")
        try:
            if id != group_id:
                monitor_status = api.get_monitor_status(id)  # No await here either
                logging.info(f"页面id为 {id} 的状态{monitor_status}")
                if monitor_status == MonitorStatus.DOWN:
                    api.delete_monitor(id)
                    logging.info(f"delete monitor {id}")
        except UptimeKumaException:
            logging.error(f"Monitor with ID {id} does not exist.")


async def edit_status_page(new_id: int):
    """
    修改Status页面
    uptime-kuma-api 库没有提供局部修改 status page 的方法，因此需要手动获取 status page 的配置，修改 monitorList，再保存

    get_status_page(str:str) 的参数为 status page 的 slug，获取方式 api.get_status_pages()，返回值是一个列表，其中的slug即为参数
    例如：
    [{'id': 1,
    'slug': 'hubuctf',
    'title': 'HUBUCTF',
    'description': None,
    'icon': '/icon.svg',
    'theme': 'auto',
    'published': True,
    'showTags': True,
    'domainNameList': [],
    'customCSS': 'body {\n  \n}\n',
    'footerText': '',
    'showPoweredBy': True,
    'googleAnalyticsId': None,
    'showCertificateExpiry': True}]
    :param new_id: 新增的监控项的ID
    """
    config = api.get_status_page('hubuctf')
    publicGroupList = config['publicGroupList']
    logging.info(f"old_publicGroupList=> {publicGroupList}")
    old_monitorList = publicGroupList[1]['monitorList']
    logging.info(f"old_monitorList=> {old_monitorList}")
    monitor_new = {'id': new_id}
    old_monitorList.append(monitor_new)
    logging.info(f"new_publicGroupList=> {publicGroupList}")
    res = api.save_status_page(slug="hubuctf", publicGroupList=publicGroupList)
    logging.info(f"res=> {res}")


async def get_monitor_name() -> list:
    """
    获取所有监控项的名称
    """
    monitored_name = []
    monitor_list = api.get_monitors()  
    for monitor in monitor_list:
        monitored_name.append(monitor['name'])
    logging.info(f"已经被监测的项目名字有monitored_name=> {monitored_name}")
    return monitored_name


async def status_page():
    """
    获取 status 页面信息
    """
    api.get_status_pages()
    a = api.get_status_page('hubuctf')
    logging.info(f"Status page: {a}")


async def main():
    """
    主函数，定时检查 pod 状态并处理监控项
    """
    while True:
        await check_pod()  # 创建
        await delete_monitor()  # 删除已关闭的（DOWN）
        await asyncio.sleep(180)
        logging.info("====" * 20 + ">" + "<" + "====" * 20)


if __name__ == "__main__":
    asyncio.run(main())

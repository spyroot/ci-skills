# all nvidia callbacks will be here

## reference collected

example

[https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/troubleshooting.html](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/troubleshooting.html)
[https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html)

- Common Chart Customization Options

<https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/nvidia-driver-configuration.html>

Table ## About the NVIDIA Driver Custom Resource

this referene how pause etc

```bash
helm upgrade <release-name> nvidia/gpu-operator \
    -n gpu-operator \
    --version=v26.7.2 \
    --reuse-values \
    --set driver.nvidiaDriverCRD.enabled=true \
    --set driver.nvidiaDriverCRD.deployDefaultCR=true \
    --set driver.upgradePolicy.autoUpgrade=true \
    --wait
```

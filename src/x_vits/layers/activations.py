# Anti-aliasing modules is adopted from https://github.com/NVIDIA/BigVGAN/tree/main/alias_free_torch
# MIT License

# Copyright (c) 2024 NVIDIA CORPORATION.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import math

import torch
import torch.nn as nn
import torch.nn.functional as F


class AntiAliasActivation(nn.Module):
    def __init__(self, channels, up=2, down=2, up_k=12, down_k=12):
        super().__init__()
        self.up = UpSample1d(up, up_k)
        self.act = SnakeBeta(channels)
        self.down = DownSample1d(down, down_k)

    def forward(self, x):
        x = self.up(x)
        x = self.act(x)
        x = self.down(x)
        return x


class Snake(nn.Module):
    def __init__(self, channels):
        super().__init__()
        self.alpha = nn.Parameter(torch.zeros(1, channels, 1))

    def forward(self, x):
        alpha = self.alpha.exp()
        x = x + (1.0 / (alpha + 1e-9)) * (x * alpha).sin().pow(2)
        return x


class SnakeBeta(nn.Module):
    def __init__(self, channels):
        super().__init__()
        self.alpha = nn.Parameter(torch.zeros(1, channels, 1))
        self.beta = nn.Parameter(torch.zeros(1, channels, 1))

    def forward(self, x):
        alpha = self.alpha.exp()
        beta = self.beta.exp()
        x = x + (1.0 / (beta + 1e-9)) * (x * alpha).sin().pow(2)
        return x


def kaiser_sinc_filter1d(cutoff, half_width, kernel_size):
    even = kernel_size % 2 == 0
    half_size = kernel_size // 2

    delta_f = 4 * half_width
    A = 2.285 * (half_size - 1) * math.pi * delta_f + 7.95
    if A > 50.0:
        beta = 0.1102 * (A - 8.7)
    elif A >= 21.0:
        beta = 0.5842 * (A - 21) ** 0.4 + 0.07886 * (A - 21.0)
    else:
        beta = 0.0
    window = torch.kaiser_window(kernel_size, beta=beta, periodic=False)

    if even:
        time = torch.arange(-half_size, half_size) + 0.5
    else:
        time = torch.arange(kernel_size) - half_size
    if cutoff == 0:
        filter_ = torch.zeros_like(time)
    else:
        filter_ = 2 * cutoff * window * torch.sinc(2 * cutoff * time)
        filter_ /= filter_.sum()
        filter = filter_.view(1, 1, kernel_size)
    return filter


class UpSample1d(nn.Module):
    def __init__(self, ratio=2, kernel_size=None):
        super().__init__()
        self.ratio = ratio
        kernel_size = int(6 * ratio // 2) * 2 if kernel_size is None else kernel_size
        self.stride = ratio
        self.pad = kernel_size // ratio - 1
        self.pad_left = self.pad * self.stride + (kernel_size - self.stride) // 2
        self.pad_right = self.pad * self.stride + (kernel_size - self.stride + 1) // 2
        filter = kaiser_sinc_filter1d(
            cutoff=0.5 / ratio, half_width=0.6 / ratio, kernel_size=kernel_size
        )
        self.register_buffer("filter", filter)

    def forward(self, x):
        _, C, _ = x.shape
        x = F.pad(x, (self.pad, self.pad), mode="replicate")
        x = self.ratio * F.conv_transpose1d(
            x, self.filter.expand(C, -1, -1), stride=self.stride, groups=C
        )
        x = x[..., self.pad_left : -self.pad_right]  # noqa
        return x


class LowPassFilter1d(nn.Module):
    def __init__(
        self, cutoff=0.5, half_width=0.6, stride: int = 1, kernel_size: int = 12
    ):
        super().__init__()
        if cutoff < -0.0:
            raise ValueError("Minimum cutoff must be larger than zero.")
        if cutoff > 0.5:
            raise ValueError("A cutoff above 0.5 does not make sense.")
        even = kernel_size % 2 == 0
        self.pad_left = kernel_size // 2 - int(even)
        self.pad_right = kernel_size // 2
        self.stride = stride
        filter = kaiser_sinc_filter1d(cutoff, half_width, kernel_size)
        self.register_buffer("filter", filter)

    def forward(self, x):
        _, C, _ = x.shape
        x = F.pad(x, (self.pad_left, self.pad_right), mode="replicate")
        out = F.conv1d(x, self.filter.expand(C, -1, -1), stride=self.stride, groups=C)
        return out


class DownSample1d(nn.Module):
    def __init__(self, ratio=2, kernel_size=None):
        super().__init__()
        self.ratio = ratio
        self.kernel_size = (
            int(6 * ratio // 2) * 2 if kernel_size is None else kernel_size
        )
        self.lowpass = LowPassFilter1d(
            cutoff=0.5 / ratio,
            half_width=0.6 / ratio,
            stride=ratio,
            kernel_size=self.kernel_size,
        )

    def forward(self, x):
        x = self.lowpass(x)
        return x
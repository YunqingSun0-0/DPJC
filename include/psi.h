#pragma once
#include <string>
#include <vector>
#include <functional>
#include <emp-tool/emp-tool.h>

using PsiFunc = std::function<int(int, const std::vector<int>&, emp::NetIO*)>;

void register_psi_method(const std::string& name, PsiFunc func);
int compute_psi_ca(int party, const std::vector<int>& set, emp::NetIO* io);

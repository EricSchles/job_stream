
#include "job.h"
#include "message.h"
#include "module.h"
#include "processor.h"
#include "yaml.h"

#include <exception>

namespace job_stream {
namespace job {

JobBase::JobBase() {
}


JobBase::~JobBase() {
}


std::string JobBase::getFullName() const {
    if (this->parent) {
        return this->parent->getFullName() + "::" + this->id;
    }
    return this->id;
}


void JobBase::setup(processor::Processor* processor, module::Module* parent,
        const std::string& id, const YAML::Node& config, 
        const YAML::Node& globalConfig) {
    this->processor = processor;
    this->parent = parent;
    this->id = id;
    this->config = config;
    this->globalConfig = globalConfig;
}


void JobBase::sendModuleOutput(const std::string& payload) {
    std::vector<std::string> target = this->currentRecord->getTarget();
    //sendModuleOutput() is called in the context of a Reducer, meaning
    //target was the original record - that is, it points to our module.

    if (this->parent->getLevel() != 0) {
        const YAML::Node& parentTo = this->parent->getConfig()["to"];
        if (!parentTo) {
            std::ostringstream ss;
            ss << "Module " << this->parent->getFullName() << " needs 'to'";
            throw std::runtime_error(ss.str());
        }
        this->sendTo(parentTo, payload);
        return;
    }

    target.push_back("output");
    target.push_back("reduced");
    message::WorkRecord wr(target, payload);
    wr.chainFrom(*this->currentRecord);
    this->processor->sendWork(wr);
}


void JobBase::sendTo(const YAML::Node& targetList, const std::string& payload) {
    std::vector<std::string> target = this->currentRecord->getTarget();
    auto targetAsList = targetList.as<YAML::NodeList>();
    for (int i = 0, m = targetAsList.size(); i < m; i++) {
        //On our first send, target includes the job (it already did).  But, we
        //want to redirect to targetList based on the module level.  So we 
        //always pop the last part of target.
        target.pop_back();
        target.push_back(targetAsList[i].as<std::string>());
        message::WorkRecord wr(target, payload);
        wr.chainFrom(*this->currentRecord);
        this->processor->sendWork(wr);
    }
}


} //job
} //job_stream